"""Repeatable, non-LLM evaluation metrics for completed investigations.

Scores only objective, mechanically-checkable properties of a single
finished investigation. It deliberately does NOT try to judge semantic
root-cause correctness by string similarity — that would fake a metric the
system can't honestly claim. Where a metric can't be scored for a given
scenario (e.g. no labelled doc refs, no decoy hypothesis to challenge the
critic), it returns ``None`` and records why in ``notes`` rather than
inventing a number.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from app.schemas.evidence import EvidenceLedger
from app.schemas.scenario import IncidentScenario


@dataclass
class EvaluationResult:
    scenario_id: str
    root_cause_evidence_recall: float
    citation_precision: float
    retrieval_recall: Optional[float]
    critic_effective: Optional[bool]
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _all_claim_refs(ledger: EvidenceLedger) -> list[str]:
    """Every citation asserted by a reasoning agent (not the raw evidence
    itself). Used to measure citation precision across the whole ledger."""
    refs: list[str] = []
    for hypothesis in ledger.hypotheses:
        refs.extend(hypothesis.supporting_evidence_refs)
        refs.extend(hypothesis.contradicting_evidence_refs)
    if ledger.recommendation is not None:
        refs.extend(ledger.recommendation.supporting_evidence_refs)
    if ledger.postmortem is not None:
        for statement in (
            ledger.postmortem.executive_summary
            + ledger.postmortem.timeline
            + ledger.postmortem.recommended_actions
            + ([ledger.postmortem.root_cause] if ledger.postmortem.root_cause else [])
        ):
            refs.extend(statement.supporting_evidence_refs)
    return refs


def evaluate_investigation(scenario: IncidentScenario, ledger: EvidenceLedger) -> EvaluationResult:
    """Score only objective, inspectable properties of one investigation.

    ``root_cause_evidence_recall`` checks whether the top surviving hypothesis
    cites the evidence the scenario expects. It intentionally does not claim
    semantic root-cause accuracy; that requires a labelled assertion matcher
    or manual review and should never be faked by string similarity.
    """
    notes: list[str] = []
    # Partition expected refs: doc refs are scored by retrieval_recall, while
    # investigative refs (metric/log/commit) are what a hypothesis is expected
    # to cite. Mixing them would let a missing runbook citation drag down
    # root-cause recall, or vice versa.
    expected = set(scenario.expected_evidence_refs)
    expected_docs = {ref for ref in expected if ref.startswith("doc:")}
    expected_evidence = expected - expected_docs

    survivors = [h for h in ledger.hypotheses if h.status == "survived_critique"]
    top = max(survivors, key=lambda h: h.confidence) if survivors else None
    supported = set(top.supporting_evidence_refs) if top else set()
    root_cause_evidence_recall = (
        len(expected_evidence & supported) / len(expected_evidence) if expected_evidence else 0.0
    )
    if top is None:
        notes.append("No hypothesis survived critique; root-cause evidence recall is 0.")

    claim_refs = _all_claim_refs(ledger)
    citation_precision = (
        (len(claim_refs) - len(ledger.unresolved_refs(claim_refs))) / len(claim_refs)
        if claim_refs
        else 1.0
    )

    retrieved_docs = {e.source_ref for e in ledger.evidence if e.source_type.value == "doc"}
    retrieval_recall = len(expected_docs & retrieved_docs) / len(expected_docs) if expected_docs else None
    if retrieval_recall is None:
        notes.append("No expected document refs are labelled for this scenario; retrieval recall is not scored.")

    decoy_refs = {f"commit:{c.sha}" for c in scenario.deploy_history if not c.is_guilty_commit}
    decoy_hypotheses = [h for h in ledger.hypotheses if decoy_refs & set(h.supporting_evidence_refs)]
    if not scenario.red_herrings:
        critic_effective = None
    elif not decoy_hypotheses:
        critic_effective = None
        notes.append("No hypothesis cited a decoy commit, so critic effectiveness was not challenged or scored.")
    else:
        critic_effective = all(h.status == "demoted" for h in decoy_hypotheses)

    return EvaluationResult(
        scenario_id=scenario.scenario_id,
        root_cause_evidence_recall=root_cause_evidence_recall,
        citation_precision=citation_precision,
        retrieval_recall=retrieval_recall,
        critic_effective=critic_effective,
        notes=notes,
    )
