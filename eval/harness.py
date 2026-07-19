"""Non-LLM eval metrics for a finished investigation.

Scores only objective, mechanically-checkable things. It does NOT judge semantic
root-cause correctness by string similarity — that would fake a metric we can't
honestly claim. When a metric can't be scored (no labelled doc refs, no decoy to
challenge the critic), it returns None and says why in `notes`.
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


def runbook_of(doc_ref: str) -> str:
    """Drop the section anchor from a doc ref:
    'doc:runbook-db-connection-pool#common-root-causes' -> 'doc:runbook-db-connection-pool'.
    Retrieval recall is scored per runbook, not per section — several sections of
    the right runbook are all valid citations."""
    return doc_ref.split("#", 1)[0]


def _all_claim_refs(ledger: EvidenceLedger) -> list[str]:
    """Every ref a reasoning agent cited (not the raw evidence). Used for
    citation precision."""
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
    """Score the objective, inspectable properties of one investigation.

    root_cause_evidence_recall checks whether the top surviving hypothesis cites
    the expected evidence — not semantic accuracy, which we don't fake."""
    notes: list[str] = []
    # Split expected refs: doc refs are scored by retrieval_recall; the rest
    # (metric/log/commit) are what a hypothesis should cite. Mixing them would
    # let a missing runbook citation drag down root-cause recall.
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

    expected_runbooks = {runbook_of(ref) for ref in expected_docs}
    retrieved_runbooks = {
        runbook_of(e.source_ref) for e in ledger.evidence if e.source_type.value == "doc"
    }
    retrieval_recall = (
        len(expected_runbooks & retrieved_runbooks) / len(expected_runbooks)
        if expected_runbooks
        else None
    )
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
