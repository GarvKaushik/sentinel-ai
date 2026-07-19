"""The web API.

Routes:
  GET  /health                      is it alive
  GET  /incidents                   list the built-in demo incidents
  POST /investigate                 run a full investigation on a posted incident
  POST /investigate/{incident_id}   run one on a built-in incident
  POST /alert                       investigate a LIVE incident from telemetry
  GET  /investigations[/{id}]       past runs (history) + one run's full result
  GET  /smoke-test/evidence-ledger  quick schema self-check, no LLM

/investigate runs the pipeline inline (tens of seconds). /alert instead queues
the work on Redis and returns a job id; a Celery worker runs it and saves the
result to Postgres, which you poll via /investigations/{id}.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.schemas.evidence import EvidenceLedger, EvidenceObject, SourceType
from app.schemas.scenario import IncidentScenario
from app.ingestion.loader import load_incident
from app.ingestion.adapter import build_incident_from_alert
from app.pipeline import run_investigation
from app.agents.postmortem import render_markdown
from app.db import repository as repo
from app.db.base import engine_available, init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Make the investigations table if a DB is set up. If not, the API still
    # runs — it just won't keep any history.
    if init_db():
        print("Postgres persistence: schema ready.")
    else:
        print("Postgres persistence: disabled (DATABASE_URL unset or DB unreachable).")
    yield


app = FastAPI(title="Sentinel AI", version="0.1.0", lifespan=lifespan)

INCIDENTS_DIR = Path(__file__).resolve().parents[1] / "data" / "incidents"


def _catalogue_ids() -> list[str]:
    """Names of the built-in incident folders."""
    if not INCIDENTS_DIR.exists():
        return []
    return sorted(p.name for p in INCIDENTS_DIR.iterdir() if p.is_dir() and (p / "metadata.json").exists())


def _ledger_response(ledger: EvidenceLedger) -> dict:
    """Turn a finished investigation into the JSON the API returns."""
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
    """The built-in incidents you can run by id."""
    return {"incidents": _catalogue_ids()}


@app.post("/investigate")
def investigate(scenario: IncidentScenario):
    """Run a full investigation on an incident sent in the request body."""
    ledger = run_investigation(scenario)
    resp = _ledger_response(ledger)
    resp["investigation_id"] = repo.save_completed(ledger, trigger="manual")
    return resp


class AlertTrigger(BaseModel):
    service: str
    metric: str = "error_rate_pct"
    window_minutes: int = 10


@app.post("/alert")
def investigate_alert(alert: AlertTrigger):
    """Investigate a LIVE incident (what an Alertmanager webhook would call).

    If a DB + queue are set up, this drops the job on Redis and returns a job id
    right away — a worker runs it and saves the result to Postgres (poll
    /investigations/{id}). Without a DB, it just runs inline instead.
    """
    if engine_available():
        row_id = repo.create_pending(trigger="alert", service=alert.service, metric=alert.metric)
        from app.tasks import run_investigation_task  # imported here so dev without celery still works

        try:
            async_result = run_investigation_task.delay(
                row_id, alert.service, alert.metric, alert.window_minutes
            )
        except Exception as exc:  # Redis down — mark the row failed instead of leaving it stuck
            repo.mark_failed(row_id, f"could not enqueue job: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="job queue unavailable (is Redis up?)")

        repo.attach_job_id(row_id, async_result.id)
        return {
            "investigation_id": row_id,
            "job_id": async_result.id,
            "status": "queued",
            "poll": f"/investigations/{row_id}",
            "source": {"service": alert.service, "metric": alert.metric},
        }

    # No DB/queue — just run it inline.
    scenario = build_incident_from_alert(
        service=alert.service, metric=alert.metric, window_minutes=alert.window_minutes,
    )
    ledger = run_investigation(scenario)
    resp = _ledger_response(ledger)
    resp["status"] = "done"
    resp["source"] = {"service": alert.service, "metric": alert.metric, "metric_points": len(scenario.metrics)}
    return resp


@app.post("/investigate/{incident_id}")
def investigate_catalogue(incident_id: str):
    """Run a full investigation on a built-in incident by id."""
    if incident_id not in _catalogue_ids():  # must match a known folder (also blocks path traversal)
        raise HTTPException(
            status_code=404,
            detail=f"unknown incident_id '{incident_id}'. Known: {_catalogue_ids()}",
        )
    scenario = load_incident(INCIDENTS_DIR / incident_id)
    ledger = run_investigation(scenario)
    resp = _ledger_response(ledger)
    resp["investigation_id"] = repo.save_completed(ledger, trigger="catalogue")
    return resp


@app.get("/investigations")
def list_investigations(limit: int = 50):
    """Recent runs, newest first. 'enabled' is false when there's no database."""
    return {"enabled": engine_available(), "investigations": repo.list_investigations(limit=limit)}


@app.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: int):
    """One run's full stored result. Also what the cockpit polls after /alert
    until status is done or failed."""
    record = repo.get_investigation(investigation_id)
    if record is None:
        detail = (
            "persistence is disabled (no DATABASE_URL)"
            if not engine_available()
            else f"no investigation with id {investigation_id}"
        )
        raise HTTPException(status_code=404, detail=detail)
    return record


@app.get("/smoke-test/evidence-ledger")
def smoke_test_evidence_ledger():
    """Build a tiny ledger by hand to check the core schema works: add evidence,
    resolve a real citation, and flag a fake one. No LLM involved."""
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
    fake_ref = "log:svc-payments:line:9999999"  # not in the ledger — should be flagged

    return {
        "incident_id": ledger.incident_id,
        "evidence_count": len(ledger.evidence),
        "valid_citation_check": ledger.unresolved_refs(valid_refs),  # expect: []
        "invalid_citation_check": ledger.unresolved_refs([fake_ref]),  # expect: [fake_ref]
    }
