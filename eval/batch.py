"""Batch evaluation across Sentinel AI's incident catalogue.

The catalogue is the set of ``data/incidents/<id>/`` folders, each a JSON
incident loaded via ``app.ingestion.loader.load_incident``. Keeping the
catalogue as data (not code) makes it cheap to hand-add scenarios and mirrors
how real incident telemetry would arrive.

Two entry points:

* ``run_correlator_coverage`` — deterministic only, no LLM/Qdrant, no API key.
  Confirms the rule-based correlator surfaces every ``expected_evidence_ref``
  before any model reasons over the evidence. This is the default command.
* ``evaluate_batch`` — runs a full, LLM-backed investigation pipeline over the
  catalogue and scores each result with ``eval.harness``. Makes paid model
  calls, so it is never the default; pass ``run_full_pipeline`` to it after
  setting ``GROQ_API_KEY`` and ingesting the runbooks.
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
    """Load every incident folder under ``data/incidents`` in sorted order.

    A folder counts as an incident only if it has a ``metadata.json``; this
    skips stray files and keeps discovery deterministic."""
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
    # The correlator produces metric/log/commit evidence only; doc refs are the
    # retriever's job and are scored separately by retrieval_recall.
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


def run_full_pipeline(scenario: IncidentScenario) -> EvidenceLedger:
    """Run all agents for an LLM-backed evaluation of one scenario.

    Not the module default: it makes paid external model calls and loads the
    embedding model. Pass it to ``evaluate_batch`` after setting
    ``GROQ_API_KEY`` and ingesting the runbooks into a running Qdrant."""
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
