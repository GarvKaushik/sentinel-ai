"""Recommendation agent — proposes a concrete, grounded fix.

Takes the top surviving hypothesis, pulls remediation guidance from the runbooks
(reusing the Retriever), and asks the LLM for an actionable fix.

Same citation rule as the others, plus one extra: because this recommends an
ACTION on production, if the fix can't be grounded after stripping bad refs it
escalates to a human rather than presenting an unverified "fix".
"""

from __future__ import annotations
import json

from app.schemas.evidence import EvidenceLedger, Recommendation, Hypothesis
from app.llm.client import chat
from app.retrieval.ingest import get_qdrant_client
from app.retrieval.search import search_runbooks

RECOMMENDATION_MODEL = "openai/gpt-oss-120b"

FALLBACK_ESCALATION = Recommendation(
    summary="Escalate to on-call human — insufficient grounded evidence to safely auto-recommend a fix.",
    detailed_steps=[],
    supporting_evidence_refs=[],
    risk_notes=(
        "The Recommendation agent's proposed action could not be fully "
        "grounded in verified evidence after citation validation. Rather "
        "than present an unverified fix, this incident is flagged for "
        "manual review."
    ),
    is_fallback_escalation=True,
)


def get_top_hypothesis(ledger: EvidenceLedger) -> Hypothesis | None:
    """The highest-confidence hypothesis that survived critique. Demoted ones are
    never used, even if their number looks decent."""
    survivors = [h for h in ledger.hypotheses if h.status == "survived_critique"]
    if not survivors:
        return None
    return max(survivors, key=lambda h: h.confidence)


def build_remediation_query(hypothesis: Hypothesis) -> str:
    return f"remediation fix rollback for: {hypothesis.description}"


def _build_recommendation_prompt(ledger: EvidenceLedger, hypothesis: Hypothesis) -> str:
    evidence_lines = "\n".join(
        f'- ref="{e.source_ref}": {e.claim}'
        for e in ledger.evidence
        if e.source_ref in hypothesis.supporting_evidence_refs or e.source_type.value == "doc"
    )
    return f"""The investigation has concluded the most likely root cause is:

"{hypothesis.description}" (confidence={hypothesis.confidence:.2f})

Relevant evidence, including retrieved runbook remediation guidance:
{evidence_lines}

Propose a concrete, actionable fix. Rules:
- supporting_evidence_refs must be refs that appear EXACTLY as written
  above. Do not invent refs.
- Base the recommendation on what the evidence and runbook guidance
  actually say — do not propose generic advice unconnected to the cited
  evidence.
- detailed_steps should be concrete and ordered (e.g. "1. Roll back
  commit X", "2. Add regression test for Y").

Respond ONLY with a JSON object in this exact shape, no other text:
{{
  "summary": "one-sentence recommended action",
  "detailed_steps": ["step 1", "step 2"],
  "supporting_evidence_refs": ["ref1", "ref2"],
  "risk_notes": "any caveats or risks with this recommendation, or null"
}}
"""


def run_recommendation(ledger: EvidenceLedger, use_in_memory_qdrant: bool = False) -> EvidenceLedger:
    top_hyp = get_top_hypothesis(ledger)
    if top_hyp is None:
        print("  [recommendation] No surviving hypothesis to base a recommendation on — escalating.")
        ledger.recommendation = FALLBACK_ESCALATION
        return ledger

    # Pull remediation guidance from the runbooks for this hypothesis.
    from app.retrieval.embeddings import get_embedder
    client = get_qdrant_client(in_memory=use_in_memory_qdrant)
    embedder = get_embedder()

    query = build_remediation_query(top_hyp)
    remediation_evidence = search_runbooks(query, client, embedder, top_k=2, produced_by="recommendation_agent")
    for e in remediation_evidence:
        # skip chunks already in the ledger
        if ledger.resolve_ref(e.source_ref) is None:
            ledger.add_evidence(e)

    raw = chat(
        prompt=_build_recommendation_prompt(ledger, top_hyp),
        system="You are an SRE proposing a remediation. Never cite evidence that wasn't given to you.",
        model=RECOMMENDATION_MODEL,
        temperature=0.1,
        json_mode=True,
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Recommendation agent LLM did not return valid JSON: {raw!r}") from e

    proposed_refs = parsed.get("supporting_evidence_refs", [])
    bad_refs = ledger.unresolved_refs(proposed_refs)
    valid_refs = [r for r in proposed_refs if r not in bad_refs]

    if bad_refs:
        print(f"  [validation] recommendation cited unresolved refs, stripped: {bad_refs}")

    if not valid_refs:
        print("  [validation] recommendation had zero valid refs after stripping — falling back to escalation")
        ledger.recommendation = FALLBACK_ESCALATION
        return ledger

    ledger.recommendation = Recommendation(
        summary=parsed.get("summary", ""),
        detailed_steps=parsed.get("detailed_steps", []),
        supporting_evidence_refs=valid_refs,
        risk_notes=parsed.get("risk_notes"),
        is_fallback_escalation=False,
    )
    return ledger


if __name__ == "__main__":
    from pathlib import Path
    import importlib.util
    from app.agents.critic import run_critic
    from app.agents.root_cause import run_root_cause_investigation

    spec = importlib.util.spec_from_file_location(
        "scenario_001", Path(__file__).resolve().parents[2] / "data/scenarios/scenario_001_bad_deploy.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ledger = run_root_cause_investigation(mod.scenario_001)
    ledger = run_critic(ledger)
    ledger = run_recommendation(ledger)

    print("\nRECOMMENDATION:")
    rec = ledger.recommendation
    print(f"  Summary: {rec.summary}")
    print(f"  Fallback escalation: {rec.is_fallback_escalation}")
    print("  Steps:")
    for i, step in enumerate(rec.detailed_steps, 1):
        print(f"    {i}. {step}")
    print(f"  Supporting refs: {rec.supporting_evidence_refs}")
    if rec.risk_notes:
        print(f"  Risk notes: {rec.risk_notes}")
