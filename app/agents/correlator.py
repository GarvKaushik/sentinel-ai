"""Correlator — finds the anomalies, deterministically.

Detection is plain rules (threshold on metrics, service-inference on commits,
time-window on logs), not an LLM — cheaper and more reliable, and the evidence
it produces is grounded in arithmetic. The LLM is used only at the very end to
write a short plain-English summary for the dashboard; that summary is NOT
evidence and has no source_ref.
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
COMMIT_CORRELATION_WINDOW_SECONDS = 300  # (5 minutes)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def detect_metric_anomalies(metrics: list[MetricPoint]) -> dict[str, dict]:
    """Per service: baseline = mean of the first half of points; flag the first
    later point above baseline * ANOMALY_THRESHOLD_MULTIPLIER. Returns
    {service: {"baseline", "onset_point"}} for services with an anomaly."""
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
    """Guess a commit's service by matching the service name against its
    changed file paths."""

    for service in known_services:
        # match against "checkout", not "svc-checkout"
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
    """For each commit: is it a plausible cause? (same service as an anomaly AND
    landed just before its onset). Emits an EvidenceObject either way — hits and
    explicit rule-outs — so the Critic has something concrete to point at."""

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


def run_correlator(
    scenario: IncidentScenario,
    include_summary: bool = True,
) -> tuple[EvidenceLedger, str]:
    """Run the full Correlator pass. Returns the ledger plus a plain-English LLM
    summary (narration, not citable evidence).

    include_summary=False skips the LLM call, so the deterministic stage runs
    with no API key — that's what the batch evaluator uses."""

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

    # 2. WARN/ERROR/FATAL logs. WARN is included because during an incident a
    # warning (e.g. "slow downstream") is often the best clue — but it gets
    # lower confidence than ERROR/FATAL.
    for log in scenario.logs:
        if log.level in ("WARN", "ERROR", "FATAL"):
            ledger.add_evidence(
                EvidenceObject(
                    claim=f"{log.level} in {log.service}: {log.message}",
                    source_type=SourceType.LOG,
                    source_ref=log.line_id,
                    confidence=0.6 if log.level == "WARN" else 0.9,
                    timestamp=_parse_ts(log.timestamp),
                    produced_by="correlator_agent",
                )
            )

    # 3. Deploy correlation (includes explicit rule-outs, not just hits)
    for ev in correlate_deploys(scenario.deploy_history, anomalies, known_services):
        ledger.add_evidence(ev)

    # 4. LLM summary — just narration over the evidence above, no new claims.
    if not include_summary:
        return ledger, "LLM summary disabled."

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
