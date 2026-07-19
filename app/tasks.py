"""Celery app + the async investigation task.

The pipeline takes ~20s, so running it inside the HTTP request blocks a worker
and times out under load. Instead: /alert makes a 'queued' row, drops a job on
Redis, and returns an id immediately; this worker (a separate container) pulls
the job, runs the pipeline, and saves the result to Postgres, which you poll.

Run the worker (docker):  celery -A app.tasks:celery_app worker --concurrency=2
On Windows dev add --pool=solo.
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
    """Build the incident from live telemetry, run the pipeline, save the result.

    Pipeline modules are imported lazily so the web process can import this just
    to enqueue, without pulling in the heavy deps."""
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
