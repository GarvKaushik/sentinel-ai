"""Celery app + the async investigation task.

Why this exists: the pipeline makes several LLM calls and takes ~20s. Running it
inside the HTTP request blocks a worker for the whole time and makes /alert time
out under load. Instead:

    POST /alert  ->  create a 'queued' row in Postgres, drop a job on Redis,
                     return {investigation_id} immediately
    Redis        ->  holds the job queue (the "broker")
    this worker  ->  a SEPARATE process/container that pulls the job, runs the
                     pipeline, and writes the result back to Postgres
    poll         ->  GET /investigations/{id} until status is done|failed

Run the worker (Linux / docker-compose):
    celery -A app.tasks:celery_app worker --loglevel=info --concurrency=2
On Windows dev add --pool=solo (the default prefork pool doesn't run on Windows).
"""

from __future__ import annotations

import os

from celery import Celery

from app.db import repository as repo

# Both default to localhost so a dev worker works without compose; in compose
# these point at the redis service (see docker-compose.yml).
BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

celery_app = Celery("sentinel", broker=BROKER_URL, backend=RESULT_BACKEND)
celery_app.conf.update(
    task_track_started=True,          # expose a STARTED state, not just PENDING->SUCCESS
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_time_limit=300,              # hard-kill a stuck pipeline after 5 min
    task_soft_time_limit=240,         # raise SoftTimeLimitExceeded first for cleanup
    worker_max_tasks_per_child=50,    # recycle workers to bound any memory creep
    broker_connection_retry_on_startup=True,
)


@celery_app.task(name="app.tasks.run_investigation_task")
def run_investigation_task(row_id: int | None, service: str, metric: str, window_minutes: int) -> dict:
    """Build the incident from live telemetry, run the full pipeline, persist it.

    Heavy pipeline modules are imported lazily so the web process can import this
    module just to enqueue without pulling in torch/qdrant/etc."""
    from app.ingestion.adapter import build_incident_from_alert
    from app.pipeline import run_investigation

    repo.mark_running(row_id)
    try:
        scenario = build_incident_from_alert(
            service=service, metric=metric, window_minutes=window_minutes
        )
        ledger = run_investigation(scenario)
        repo.mark_done(row_id, ledger)
        return {"investigation_id": row_id, "status": "done", "incident_id": ledger.incident_id}
    except Exception as exc:
        repo.mark_failed(row_id, f"{type(exc).__name__}: {exc}")
        raise
