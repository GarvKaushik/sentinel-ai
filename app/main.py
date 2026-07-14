"""
Sentinel AI — FastAPI entrypoint.

Week 1 goal: prove the schema + one scenario load correctly and can be
served over HTTP. No agents, no RAG yet — just the skeleton and a smoke test.
"""

from fastapi import FastAPI
from app.schemas.evidence import EvidenceLedger, EvidenceObject, SourceType

app = FastAPI(title="Sentinel AI", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


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
