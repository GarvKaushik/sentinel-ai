"""Batch evaluation for Sentinel AI's scenario catalogue."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Callable

from app.agents.correlator import run_correlator
from app.schemas.evidence import EvidenceLedger
from app.schemas.scenario import IncidentScenario
from data.scenarios.library import all_scenarios
from eval.harness import EvaluationResult, evaluate_investigation


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
    expected = set(scenario.expected_evidence_refs)
    missing = sorted(expected - found_refs)
    recall = (len(expected) - len(missing)) / len(expected) if expected else 1.0
    return CorrelatorCoverageResult(scenario.scenario_id, recall, missing)


def run_correlator_coverage(scenarios: list[IncidentScenario] | None = None) -> BatchSummary:
    results = [evaluate_correlator_coverage(scenario) for scenario in (scenarios or all_scenarios())]
    return BatchSummary(
        scenario_count=len(results),
        mean_expected_evidence_recall=mean(result.expected_evidence_recall for result in results) if results else 0.0,
        results=results,
    )


def evaluate_batch(
    pipeline: Callable[[IncidentScenario], EvidenceLedger],
    scenarios: list[IncidentScenario] | None = None,
) -> list[EvaluationResult]:
    """Run a fully configured investigation pipeline and score every result."""
    return [evaluate_investigation(scenario, pipeline(scenario)) for scenario in (scenarios or all_scenarios())]


def run_full_pipeline(scenario: IncidentScenario) -> EvidenceLedger:
    """Run all agents in local, in-memory mode for an LLM-backed evaluation.

    This intentionally is not the module's default command because it makes
    paid external model calls and loads the embedding model. Use it as the
    callable passed to ``evaluate_batch`` after setting ``GROQ_API_KEY``.
    """
    from app.agents.critic import run_critic
    from app.agents.postmortem import generate_postmortem
    from app.agents.recommendation import run_recommendation
    from app.agents.root_cause import run_root_cause_investigation

    ledger = run_root_cause_investigation(scenario, use_in_memory_qdrant=True)
    ledger = run_critic(ledger)
    ledger = run_recommendation(ledger, use_in_memory_qdrant=True)
    generate_postmortem(ledger, title=scenario.title)
    return ledger


if __name__ == "__main__":
    summary = run_correlator_coverage()
    print(f"Scenarios: {summary.scenario_count}")
    print(f"Mean expected-evidence recall: {summary.mean_expected_evidence_recall:.2%}")
    for result in summary.results:
        outcome = "PASS" if not result.missing_refs else f"MISSING {result.missing_refs}"
        print(f"  {result.scenario_id}: {result.expected_evidence_recall:.2%} {outcome}")
