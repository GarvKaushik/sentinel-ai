"""
Correlator Agent.

Deliberately rule-based for the actual anomaly DETECTION — threshold
checks on metrics, service-inference on commits, time-window filtering
on logs. This is more reliable and far cheaper than asking an LLM to
eyeball a time series, and it means the EvidenceObjects this agent
produces are grounded in arithmetic, not a model's guess.

The LLM (via app/llm/client.py) is used ONLY at the very end, to write
a one-paragraph plain-English summary of what was found — for the
investigation dashboard/trace view. That summary is explicitly NOT
itself an EvidenceObject and carries no source_ref — it's narration
over evidence that's already been established deterministically.
"""

from __future__ import annotations
from datetime import datetime

from app.schemas.evidence import EvidenceObject, SourceType, EvidenceLedger
from app.schemas.scenario import IncidentScenario, MetricPoint, CommitInfo
from app.llm.client import chat

# How many multiples of baseline a metric must exceed to count as an anomaly.
ANOMALY_THRESHOLD_MULTIPLIER = 3.0

# A commit is only considered a plausible cause if it landed within this
# many seconds BEFORE the anomaly onset for its inferred service.
COMMIT_CORRELATION_WINDOW_SECONDS = 300  # 5 minutes


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def detect_metric_anomalies(metrics: list[MetricPoint]) -> dict[str, dict]:
    """
    Per service, find the baseline (mean of the first half of points) and
    the first point that exceeds baseline * ANOMALY_THRESHOLD_MULTIPLIER.
    Returns {service_name: {"baseline": float, "onset_point": MetricPoint}}
    for services where an anomaly was found. Services with no anomaly are
    omitted entirely — this is what lets the Root-Cause agent later
    correctly ignore services that stayed flat (like the notifications
    decoy in scenario_001).
    """
    by_service: dict[str, list[MetricPoint]] = {}
    for m in metrics:
        by_service.setdefault(m.service, []).append(m)

    anomalies = {}
    for service, points in by_service.items():
        points = sorted(points, key=lambda p: p.timestamp)
        if len(points) < 2:
            continue

        half = max(1, len(points) // 2)
        baseline_points = points[:half]
        baseline = sum(p.value for p in baseline_points) / len(baseline_points)

        onset = None
        for p in points[half:]:
            if baseline > 0 and p.value >= baseline * ANOMALY_THRESHOLD_MULTIPLIER:
                onset = p
                break

        if onset is not None:
            anomalies[service] = {"baseline": baseline, "onset_point": onset}

    return anomalies


def infer_commit_service(commit: CommitInfo, known_services: list[str]) -> str | None:
    """Guess which service a commit belongs to by checking whether the
    service name appears anywhere in the changed file paths. Crude but
    effective for realistic monorepo/microservice path conventions
    (e.g. 'src/main/java/payments/...' -> 'svc-payments' if 'payments'
    is in the service name)."""

    for service in known_services:
        # strip common "svc-" prefix for matching against path segments
        bare_name = service.replace("svc-", "")
        for f in commit.files_changed:
            if bare_name.lower() in f.lower():
                return service
    return None


def correlate_deploys(
    deploy_history: list[CommitInfo],
    metric_anomalies: dict[str, dict],
    known_services: list[str],
) -> list[EvidenceObject]:
    """For each commit, check whether it plausibly explains an anomaly:
    same inferred service AND landed shortly before that service's
    anomaly onset. Commits that don't meet both conditions still get
    recorded, but as explicitly ruled-out evidence — this is what gives
    the Critic agent something concrete to point to when a red-herring
    commit gets suggested later ("ruled out: wrong service")."""

    evidence: list[EvidenceObject] = []

    for commit in deploy_history:
        inferred_service = infer_commit_service(commit, known_services)
        commit_ts = _parse_ts(commit.timestamp)

        if inferred_service and inferred_service in metric_anomalies:
            onset_ts = _parse_ts(metric_anomalies[inferred_service]["onset_point"].timestamp)
            delta_seconds = (onset_ts - commit_ts).total_seconds()

            if 0 <= delta_seconds <= COMMIT_CORRELATION_WINDOW_SECONDS:
                evidence.append(
                    EvidenceObject(
                        claim=(
                            f"Commit {commit.sha} ('{commit.message}') touches {inferred_service} "
                            f"and landed {delta_seconds:.0f}s before the anomaly onset in that service — "
                            f"plausible root-cause candidate."
                        ),
                        source_type=SourceType.COMMIT,
                        source_ref=f"commit:{commit.sha}",
                        confidence=0.75,
                        timestamp=commit_ts,
                        produced_by="correlator_agent",
                    )
                )
                continue

        # Didn't correlate — record as explicitly ruled out, not omitted.
        reason = (
            f"touches {inferred_service}, which shows no metric anomaly"
            if inferred_service and inferred_service not in metric_anomalies
            else "does not clearly touch any service with an active anomaly"
        )
        evidence.append(
            EvidenceObject(
                claim=(
                    f"Commit {commit.sha} ('{commit.message}') {reason} — "
                    f"ruled out as root-cause candidate despite deploy timing proximity."
                ),
                source_type=SourceType.COMMIT,
                source_ref=f"commit:{commit.sha}",
                confidence=0.15,
                timestamp=commit_ts,
                produced_by="correlator_agent",
            )
        )

    return evidence


def run_correlator(scenario: IncidentScenario) -> tuple[EvidenceLedger, str]:
    """Full Correlator pass over one incident scenario. Returns the
    populated EvidenceLedger plus a plain-English LLM summary (narration
    only, not itself citable evidence)."""

    ledger = EvidenceLedger(incident_id=scenario.scenario_id)

    known_services = sorted({m.service for m in scenario.metrics} | set(scenario.services_affected))

    # 1. Metric anomalies
    anomalies = detect_metric_anomalies(scenario.metrics)
    for service, info in anomalies.items():
        onset = info["onset_point"]
        ledger.add_evidence(
            EvidenceObject(
                claim=(
                    f"{service} {onset.metric_name} jumped from baseline "
                    f"~{info['baseline']:.2f} to {onset.value:.2f} at {onset.timestamp}"
                ),
                source_type=SourceType.METRIC,
                source_ref=f"prometheus:{onset.metric_name}:{service}:{onset.timestamp}",
                confidence=0.95,
                timestamp=_parse_ts(onset.timestamp),
                produced_by="correlator_agent",
            )
        )

    # 2. Error/fatal logs (all of them, for a scenario this size — at
    # production scale you'd window this to the anomaly timeframe)
    for log in scenario.logs:
        if log.level in ("ERROR", "FATAL"):
            ledger.add_evidence(
                EvidenceObject(
                    claim=f"{log.level} in {log.service}: {log.message}",
                    source_type=SourceType.LOG,
                    source_ref=log.line_id,
                    confidence=0.9,
                    timestamp=_parse_ts(log.timestamp),
                    produced_by="correlator_agent",
                )
            )

    # 3. Deploy correlation (includes explicit rule-outs, not just hits)
    for ev in correlate_deploys(scenario.deploy_history, anomalies, known_services):
        ledger.add_evidence(ev)

    # 4. LLM summary — narration only, built from already-established
    # evidence, not a source of new claims.
    evidence_lines = "\n".join(f"- {e.claim}" for e in ledger.evidence)
    summary = chat(
        prompt=(
            f"Here is evidence gathered during an automated incident investigation:\n\n"
            f"{evidence_lines}\n\n"
            "Write a 2-3 sentence plain-English summary of what this evidence suggests, "
            "for an on-call engineer glancing at a dashboard. Do not invent any facts "
            "beyond what's listed above."
        ),
        system="You are an SRE assistant summarizing incident evidence. Be terse and factual.",
    )

    return ledger, summary


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "scenario_001", Path(__file__).resolve().parents[2] / "data/scenarios/scenario_001_bad_deploy.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ledger, summary = run_correlator(mod.scenario_001)

    print(f"Incident: {ledger.incident_id}")
    print(f"Evidence collected: {len(ledger.evidence)}\n")
    for e in ledger.evidence:
        print(f"  [{e.source_type.value:8s} conf={e.confidence:.2f}] {e.claim}")
    print(f"\nLLM Summary:\n{summary}")
