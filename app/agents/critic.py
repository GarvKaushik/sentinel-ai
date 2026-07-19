"""
Critic Agent.

The falsification pass. Its job is explicitly adversarial: for each
hypothesis the Root-Cause agent proposed, actively hunt the evidence
ledger for anything that CONTRADICTS it — not just check whether
supporting evidence exists (the Root-Cause agent already did that).

Same citation-validity enforcement as the Root-Cause agent applies here
too: any contradicting_evidence_refs the Critic cites must resolve in
the ledger, or they get stripped. And critically — if the Critic says
"demoted" but hands back a confidence that isn't actually lower than
before (a real failure mode: LLMs sometimes say the right verdict word
but forget to move the number), we enforce the demotion in code rather
than trusting the LLM's arithmetic.
"""

from __future__ import annotations
import json

from app.schemas.evidence import EvidenceLedger
from app.llm.client import chat

CRITIC_MODEL = "openai/gpt-oss-120b"


def _build_critic_prompt(ledger: EvidenceLedger) -> str:
    evidence_lines = "\n".join(
        f'- ref="{e.source_ref}" (confidence={e.confidence:.2f}): {e.claim}'
        for e in ledger.evidence
    )
    hypothesis_lines = "\n".join(
        f'- id="{h.hypothesis_id}" (confidence={h.confidence:.2f}): {h.description}\n'
        f'  supporting_refs={h.supporting_evidence_refs}'
        for h in ledger.hypotheses
    )
    return f"""You are the Critic in an incident investigation pipeline. Your job
is to try to BREAK each hypothesis below, not confirm it. Actively search
the evidence for anything that contradicts each hypothesis, including
low-confidence "ruled out" evidence that the Correlator agent already
recorded — that evidence exists specifically to be used against
hypotheses that ignore it.

ALL EVIDENCE:
{evidence_lines}

HYPOTHESES TO CRITIQUE:
{hypothesis_lines}

For EACH hypothesis, decide: does it survive falsification, or should it
be demoted? A hypothesis should be demoted if:
- Evidence directly contradicts it (cite the contradicting ref)
- It ignores evidence that rules out something it relies on
- A competing hypothesis explains the same evidence more completely

Rules:
- contradicting_evidence_refs must be refs that appear EXACTLY as written
  above. Do not invent refs.
- If a hypothesis has no real contradiction, verdict is "survived" —
  don't manufacture a critique just to seem thorough.
- If verdict is "demoted", updated_confidence MUST be lower than the
  hypothesis's original confidence shown above.

Respond ONLY with a JSON object in this exact shape, no other text:
{{
  "critiques": [
    {{
      "hypothesis_id": "hyp_1",
      "verdict": "survived",
      "updated_confidence": 0.0,
      "contradicting_evidence_refs": [],
      "rationale": "one sentence explaining the verdict"
    }}
  ]
}}
"""


def run_critic(ledger: EvidenceLedger) -> EvidenceLedger:
    """Runs the falsification pass over ledger.hypotheses IN PLACE
    (mutates and returns the same ledger) — updates each Hypothesis's
    status, confidence, contradicting_evidence_refs, and critic_rationale."""

    if not ledger.hypotheses:
        return ledger

    raw = chat(
        prompt=_build_critic_prompt(ledger),
        system="You are an adversarial reviewer. Your only job is to find flaws, not to be agreeable.",
        model=CRITIC_MODEL,
        temperature=0.1,
        json_mode=True,
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Critic agent LLM did not return valid JSON: {raw!r}") from e

    hyp_by_id = {h.hypothesis_id: h for h in ledger.hypotheses}

    for critique in parsed.get("critiques", []):
        hyp_id = critique.get("hypothesis_id")
        hyp = hyp_by_id.get(hyp_id)
        if hyp is None:
            print(f"  [validation] Critic referenced unknown hypothesis_id '{hyp_id}' — skipped")
            continue

        proposed_refs = critique.get("contradicting_evidence_refs", [])
        bad_refs = ledger.unresolved_refs(proposed_refs)
        valid_contradicting_refs = [r for r in proposed_refs if r not in bad_refs]
        if bad_refs:
            print(f"  [validation] {hyp_id}: stripped unresolved contradicting refs {bad_refs}")

        verdict = critique.get("verdict", "survived")
        updated_confidence = float(critique.get("updated_confidence", hyp.confidence))

        if verdict == "demoted":
            # Enforce the demotion actually demotes, regardless of what
            # number the LLM produced — a "demoted" verdict with an
            # unchanged or higher confidence is a contradiction we don't
            # trust the model to self-correct.
            if updated_confidence >= hyp.confidence:
                print(
                    f"  [validation] {hyp_id}: verdict='demoted' but confidence "
                    f"{updated_confidence:.2f} >= original {hyp.confidence:.2f} — forcing lower"
                )
                updated_confidence = max(0.0, hyp.confidence - 0.2)
            hyp.status = "demoted"
        else:
            hyp.status = "survived_critique"
            # Mirror-image defensive check: a "survived" verdict with NO
            # contradicting evidence has no justification for a confidence
            # drop either — this is a real failure mode, not hypothetical
            # (an LLM can say "survived, nothing contradicts this" and
            # still hand back confidence=0.0 for no defensible reason).
            if not valid_contradicting_refs and updated_confidence < hyp.confidence:
                print(
                    f"  [validation] {hyp_id}: verdict='survived' with zero contradicting "
                    f"evidence but confidence dropped {hyp.confidence:.2f} -> {updated_confidence:.2f} "
                    f"— keeping original confidence"
                )
                updated_confidence = hyp.confidence

        hyp.confidence = updated_confidence
        hyp.contradicting_evidence_refs = valid_contradicting_refs
        hyp.critic_rationale = critique.get("rationale", "")

    ledger.hypotheses.sort(key=lambda h: h.confidence, reverse=True)
    return ledger


if __name__ == "__main__":
    from pathlib import Path
    import importlib.util
    from app.agents.root_cause import run_root_cause_investigation

    spec = importlib.util.spec_from_file_location(
        "scenario_001", Path(__file__).resolve().parents[2] / "data/scenarios/scenario_001_bad_deploy.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ledger = run_root_cause_investigation(mod.scenario_001)
    ledger = run_critic(ledger)

    print("\nPOST-CRITIQUE HYPOTHESES:")
    for h in ledger.hypotheses:
        print(f"\n  [{h.hypothesis_id}] status={h.status} confidence={h.confidence:.2f}")
        print(f"  {h.description}")
        if h.contradicting_evidence_refs:
            print(f"  Contradicted by: {h.contradicting_evidence_refs}")
        print(f"  Critic rationale: {h.critic_rationale}")
