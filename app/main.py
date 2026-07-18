"""
Sentinel AI — FastAPI entrypoint.

Exposes the investigation pipeline over HTTP:

  GET  /health                       liveness probe
  GET  /smoke-test/evidence-ledger   schema self-check (no agents/LLM)
  GET  /incidents                    list the built-in incident catalogue
  POST /investigate                  run a full investigation on a posted incident
  POST /investigate/{incident_id}    run a full investigation on a catalogue incident

The /investigate routes run the whole LLM-backed pipeline synchronously and
can take tens of seconds; moving them behind a task queue is a planned
follow-up (see app/pipeline.py).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.schemas.evidence import EvidenceLedger, EvidenceObject, SourceType
from app.schemas.scenario import IncidentScenario
from app.ingestion.loader import load_incident
from app.pipeline import run_investigation
from app.agents.postmortem import render_markdown

app = FastAPI(title="Sentinel AI", version="0.1.0")

INCIDENTS_DIR = Path(__file__).resolve().parents[1] / "data" / "incidents"


def _catalogue_ids() -> list[str]:
    """Names of built-in incident folders (those with a metadata.json)."""
    if not INCIDENTS_DIR.exists():
        return []
    return sorted(p.name for p in INCIDENTS_DIR.iterdir() if p.is_dir() and (p / "metadata.json").exists())


def _ledger_response(ledger: EvidenceLedger) -> dict:
    """Serialize a completed investigation for the API, including a
    human-readable rendered postmortem alongside the structured ledger."""
    return {
        "incident_id": ledger.incident_id,
        "evidence_count": len(ledger.evidence),
        "hypotheses": [h.model_dump() for h in ledger.hypotheses],
        "recommendation": ledger.recommendation.model_dump() if ledger.recommendation else None,
        "postmortem": ledger.postmortem.model_dump() if ledger.postmortem else None,
        "postmortem_markdown": render_markdown(ledger.postmortem) if ledger.postmortem else None,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/incidents")
def list_incidents():
    """The built-in incident catalogue that /investigate/{incident_id} can run."""
    return {"incidents": _catalogue_ids()}


@app.post("/investigate")
def investigate(scenario: IncidentScenario):
    """Run a full investigation on an incident supplied in the request body."""
    ledger = run_investigation(scenario)
    return _ledger_response(ledger)


@app.post("/investigate/{incident_id}")
def investigate_catalogue(incident_id: str):
    """Run a full investigation on a built-in catalogue incident by id."""
    if incident_id not in _catalogue_ids():  # also rejects path traversal — id must match a known folder
        raise HTTPException(
            status_code=404,
            detail=f"unknown incident_id '{incident_id}'. Known: {_catalogue_ids()}",
        )
    scenario = load_incident(INCIDENTS_DIR / incident_id)
    ledger = run_investigation(scenario)
    return _ledger_response(ledger)


@app.get("/smoke-test/evidence-ledger")
def smoke_test_evidence_ledger():
    """
    Manually builds a tiny EvidenceLedger to confirm the schema works
    end-to-end: create evidence, add hypotheses, resolve a citation,
    and detect an invalid one. This is your Day 1 'does the core data
    model actually work' check before writing any agent logic.
    """
    ledger = EvidenceLedger(incident_id="scenario_001_bad_deploy")

    ledger.add_evidence(
        EvidenceObject(
            claim="Error rate on svc-payments jumped from 0.5% to 12.8% at 14:33 UTC",
            source_type=SourceType.METRIC,
            source_ref="prometheus:error_rate_pct:svc-payments:2026-07-06T14:33:00Z",
            confidence=0.95,
            produced_by="correlator_agent",
        )
    )
    ledger.add_evidence(
        EvidenceObject(
            claim="NullPointerException in PaymentValidator.validate referencing billing_address",
            source_type=SourceType.LOG,
            source_ref="log:svc-payments:line:4821",
            confidence=0.9,
            produced_by="correlator_agent",
        )
    )

    valid_refs = [e.source_ref for e in ledger.evidence]
    fake_ref = "log:svc-payments:line:9999999"  # doesn't exist — should be flagged

    return {
        "incident_id": ledger.incident_id,
        "evidence_count": len(ledger.evidence),
        "valid_citation_check": ledger.unresolved_refs(valid_refs),  # expect: []
        "invalid_citation_check": ledger.unresolved_refs([fake_ref]),  # expect: [fake_ref]
    }
