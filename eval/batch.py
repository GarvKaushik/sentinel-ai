"""Batch evaluation over the incident catalogue.

The catalogue is the data/incidents/<id>/ folders (JSON, not code — cheap to add
scenarios and closer to how real telemetry arrives).

Two entry points:
* run_correlator_coverage — deterministic, no LLM/Qdrant/key. Checks the
  rule-based correlator surfaces every expected ref. The default command.
* evaluate_batch — runs the full LLM pipeline over the catalogue and scores each
  result. Makes paid calls, so never the default.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Callable

from app.agents.correlator import run_correlator
from app.ingestion.loader import load_incident
from app.schemas.evidence import EvidenceLedger
from app.schemas.scenario import IncidentScenario
from eval.harness import EvaluationResult, evaluate_investigation

INCIDENTS_DIR = Path(__file__).resolve().parents[1] / "data" / "incidents"


def discover_incidents(incidents_dir: Path | None = None) -> list[IncidentScenario]:
    """Load every incident folder under data/incidents (sorted). A folder counts
    only if it has a metadata.json."""
    base = incidents_dir or INCIDENTS_DIR
    scenarios: list[IncidentScenario] = []
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        if (folder / "metadata.json").exists():
            scenarios.append(load_incident(folder))
    return scenarios


@dataclass
class CorrelatorCoverageResult:
    scenario_id: str
    expected_evidence_recall: float
    missing_refs: list[str]


@dataclass
class BatchSummary:
    scenario_count: int
    mean_expected_evidence_recall: float
    results: list[CorrelatorCoverageResult]

    def to_dict(self) -> dict:
        return {
            "scenario_count": self.scenario_count,
            "mean_expected_evidence_recall": self.mean_expected_evidence_recall,
            "results": [asdict(result) for result in self.results],
        }


def evaluate_correlator_coverage(scenario: IncidentScenario) -> CorrelatorCoverageResult:
    ledger, _ = run_correlator(scenario, include_summary=False)
    found_refs = {e.source_ref for e in ledger.evidence}
    # The correlator only makes metric/log/commit evidence; doc refs are the
    # retriever's job (scored separately by retrieval_recall).
    expected = {r for r in scenario.expected_evidence_refs if not r.startswith("doc:")}
    missing = sorted(expected - found_refs)
    recall = (len(expected) - len(missing)) / len(expected) if expected else 1.0
    return CorrelatorCoverageResult(scenario.scenario_id, recall, missing)


def run_correlator_coverage(scenarios: list[IncidentScenario] | None = None) -> BatchSummary:
    results = [evaluate_correlator_coverage(s) for s in (scenarios or discover_incidents())]
    return BatchSummary(
        scenario_count=len(results),
        mean_expected_evidence_recall=(
            mean(r.expected_evidence_recall for r in results) if results else 0.0
        ),
        results=results,
    )


def evaluate_batch(
    pipeline: Callable[[IncidentScenario], EvidenceLedger],
    scenarios: list[IncidentScenario] | None = None,
) -> list[EvaluationResult]:
    """Run a fully configured investigation pipeline and score every result."""
    return [evaluate_investigation(s, pipeline(s)) for s in (scenarios or discover_incidents())]


def evaluate_retrieval_recall(scenario: IncidentScenario, client, embedder, mode: str, top_k: int = 2) -> float | None:
    """Retrieval recall for one scenario under one mode. Rebuilds the exact query
    the Root-Cause agent uses, then scores against the expected doc refs. Needs a
    populated Qdrant. None when the scenario labels no doc refs."""
    from app.agents.root_cause import build_runbook_query
    from app.retrieval.search import search_runbooks
    from eval.harness import runbook_of

    expected_runbooks = {runbook_of(r) for r in scenario.expected_evidence_refs if r.startswith("doc:")}
    if not expected_runbooks:
        return None

    ledger, _ = run_correlator(scenario, include_summary=False)
    query = build_runbook_query(ledger)
    retrieved = {runbook_of(h.source_ref) for h in search_runbooks(query, client, embedder, top_k=top_k, mode=mode)}
    return len(expected_runbooks & retrieved) / len(expected_runbooks)


def compare_retrieval_modes(
    modes: tuple[str, ...] = ("dense", "bm25", "hybrid"),
    top_k: int = 2,
    scenarios: list[IncidentScenario] | None = None,
) -> dict[str, dict]:
    """Score retrieval recall for every scenario under each mode. Returns
    ``{mode: {"per_scenario": {id: recall}, "mean": float}}``."""
    from app.retrieval.embeddings import get_embedder
    from app.retrieval.ingest import get_qdrant_client

    scenarios = scenarios or discover_incidents()
    client = get_qdrant_client(in_memory=False)
    embedder = get_embedder()

    out: dict[str, dict] = {}
    for mode in modes:
        per_scenario = {
            s.scenario_id: evaluate_retrieval_recall(s, client, embedder, mode=mode, top_k=top_k)
            for s in scenarios
        }
        scored = [v for v in per_scenario.values() if v is not None]
        out[mode] = {
            "per_scenario": per_scenario,
            "mean": mean(scored) if scored else 0.0,
        }
    return out


def run_full_pipeline(scenario: IncidentScenario) -> EvidenceLedger:
    """Run all agents for an LLM-backed eval of one scenario. Not the default —
    makes paid calls and loads the embedder. Pass to evaluate_batch after setting
    GROQ_API_KEY and ingesting the runbooks."""
    from app.agents.critic import run_critic
    from app.agents.postmortem import generate_postmortem
    from app.agents.recommendation import run_recommendation
    from app.agents.root_cause import run_root_cause_investigation

    ledger = run_root_cause_investigation(scenario)
    ledger = run_critic(ledger)
    ledger = run_recommendation(ledger)
    generate_postmortem(ledger, title=scenario.title)
    return ledger


if __name__ == "__main__":
    summary = run_correlator_coverage()
    print(f"Scenarios: {summary.scenario_count}")
    print(f"Mean expected-evidence recall: {summary.mean_expected_evidence_recall:.2%}\n")
    for result in summary.results:
        outcome = "PASS" if not result.missing_refs else f"MISSING {result.missing_refs}"
        print(f"  {result.scenario_id}: {result.expected_evidence_recall:.2%} {outcome}")
