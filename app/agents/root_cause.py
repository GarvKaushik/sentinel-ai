"""Root-Cause agent — proposes ranked hypotheses.

Takes the Correlator's evidence + the Retriever's runbook hits and asks a
stronger LLM (gpt-oss-120b) for ranked root causes.

The key rule: every hypothesis must cite refs that actually exist in the ledger.
That's enforced in code — unresolved refs are stripped, and a hypothesis left
with none is dropped. The prompt asks for it too, but the code is what enforces
it; the LLM isn't trusted to police itself.
"""

from __future__ import annotations
import json

from app.schemas.evidence import EvidenceLedger, Hypothesis
from app.schemas.scenario import IncidentScenario
from app.llm.client import chat
from app.agents.correlator import run_correlator
from app.retrieval.ingest import get_qdrant_client
from app.retrieval.search import search_runbooks

ROOT_CAUSE_MODEL = "openai/gpt-oss-120b"


def build_runbook_query(ledger: EvidenceLedger) -> str:
    """Build the runbook search query from the Correlator's findings, so the
    search is grounded in what actually happened, not a generic query."""
    top_claims = [e.claim for e in ledger.evidence if e.confidence >= 0.7][:4]
    return " ".join(top_claims) if top_claims else "production incident investigation"


def _build_hypothesis_prompt(ledger: EvidenceLedger) -> str:
    evidence_lines = "\n".join(
        f'- ref="{e.source_ref}" (confidence={e.confidence:.2f}, type={e.source_type.value}): {e.claim}'
        for e in ledger.evidence
    )
    return f"""You are investigating a production incident. Below is all evidence
gathered so far, each with a citation ref.

EVIDENCE:
{evidence_lines}

Propose 2-4 ranked root-cause hypotheses. Rules:
- Every hypothesis MUST cite only "ref" values that appear EXACTLY as
  written above in its supporting_evidence_refs list. Do not invent refs.
- Do not propose a hypothesis with zero supporting evidence.
- A hypothesis contradicted by evidence above (e.g. a commit explicitly
  marked as ruled out) should either be omitted or given low confidence
  with the contradiction noted in the description.
- Confidence is a float between 0 and 1 reflecting how well-supported
  the hypothesis is, not how severe the incident is.

Respond ONLY with a JSON object in this exact shape, no other text:
{{
  "hypotheses": [
    {{
      "description": "plain-English root cause hypothesis",
      "confidence": 0.0,
      "supporting_evidence_refs": ["ref1", "ref2"]
    }}
  ]
}}
"""


def generate_hypotheses(ledger: EvidenceLedger) -> list[Hypothesis]:
    """Call the LLM, parse its JSON, and keep only hypotheses whose citations
    resolve in the ledger."""

    raw = chat(
        prompt=_build_hypothesis_prompt(ledger),
        system="You are a rigorous SRE root-cause analyst. You never cite evidence that wasn't given to you.",
        model=ROOT_CAUSE_MODEL,
        temperature=0.1,
        json_mode=True,
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Root-Cause agent LLM did not return valid JSON: {raw!r}") from e

    hypotheses: list[Hypothesis] = []
    for i, h in enumerate(parsed.get("hypotheses", [])):
        proposed_refs = h.get("supporting_evidence_refs", [])
        bad_refs = ledger.unresolved_refs(proposed_refs)
        valid_refs = [r for r in proposed_refs if r not in bad_refs]

        if bad_refs:
            print(f"  [validation] hypothesis {i} cited unresolved refs, stripped: {bad_refs}")

        if not valid_refs:
            print(f"  [validation] hypothesis {i} had zero valid refs after stripping — DROPPED")
            continue

        hypotheses.append(
            Hypothesis(
                hypothesis_id=f"hyp_{i+1}",
                description=h.get("description", ""),
                confidence=float(h.get("confidence", 0.0)),
                supporting_evidence_refs=valid_refs,
                status="proposed",
            )
        )

    hypotheses.sort(key=lambda h: h.confidence, reverse=True)
    return hypotheses


def run_root_cause_investigation(scenario: IncidentScenario, use_in_memory_qdrant: bool = False) -> EvidenceLedger:
    """Correlator -> Retriever (grounded by the Correlator) -> hypotheses, all
    merged into one ledger with validated hypotheses attached."""

    # 1. Correlator pass
    ledger, correlator_summary = run_correlator(scenario)
    print(f"Correlator summary: {correlator_summary}\n")

    # 2. Retriever pass, grounded by what the Correlator found
    from app.retrieval.embeddings import get_embedder
    client = get_qdrant_client(in_memory=use_in_memory_qdrant)
    embedder = get_embedder()

    query = build_runbook_query(ledger)
    print(f"Runbook query (built from Correlator evidence): {query!r}\n")
    runbook_evidence = search_runbooks(query, client, embedder, top_k=2)
    for e in runbook_evidence:
        ledger.add_evidence(e)

    # 3. Hypotheses, validated against the ledger
    hypotheses = generate_hypotheses(ledger)
    ledger.hypotheses = hypotheses

    return ledger


if __name__ == "__main__":
    from pathlib import Path
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "scenario_001", Path(__file__).resolve().parents[2] / "data/scenarios/scenario_001_bad_deploy.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ledger = run_root_cause_investigation(mod.scenario_001)

    print(f"\nTotal evidence in ledger: {len(ledger.evidence)}")
    print(f"Ground truth: {mod.scenario_001.injected_root_cause}\n")
    print("RANKED HYPOTHESES:")
    for h in ledger.hypotheses:
        print(f"\n  [{h.hypothesis_id}] confidence={h.confidence:.2f}")
        print(f"  {h.description}")
        print(f"  Supporting refs: {h.supporting_evidence_refs}")
