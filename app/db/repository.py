"""Data-access functions for the ``investigations`` table.

Every function is a no-op (returns None / []) when persistence is disabled, so
callers never have to check ``engine_available()`` themselves. Writes are also
wrapped in try/except: a persistence hiccup must never take down a live
investigation — the result is still returned to the caller, just not saved.

Two access patterns:
  * synchronous  -> ``save_completed`` writes a finished ledger in one shot.
  * asynchronous -> ``create_pending`` then ``mark_running`` / ``mark_done`` /
                    ``mark_failed`` track a Celery job's lifecycle.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from app.agents.postmortem import get_top_surviving_hypothesis, render_markdown
from app.db.base import engine_available, session_scope
from app.db.models import Investigation
from app.schemas.evidence import EvidenceLedger

log = logging.getLogger(__name__)


def _top(ledger: EvidenceLedger) -> tuple[Optional[str], Optional[float]]:
    top = get_top_surviving_hypothesis(ledger)
    return (top.description, top.confidence) if top else (None, None)


def _apply_result(row: Investigation, ledger: EvidenceLedger) -> None:
    """Copy a finished ledger's summary + payload onto a row."""
    desc, conf = _top(ledger)
    row.status = "done"
    row.incident_id = ledger.incident_id
    row.top_root_cause = desc
    row.confidence = conf
    row.evidence_count = len(ledger.evidence)
    row.ledger_json = ledger.model_dump(mode="json")
    row.postmortem_markdown = render_markdown(ledger.postmortem) if ledger.postmortem else None
    row.error = None


# --- synchronous path (used by /investigate and /investigate/{id}) ----------
def save_completed(
    ledger: EvidenceLedger,
    *,
    trigger: str = "manual",
    service: Optional[str] = None,
    metric: Optional[str] = None,
) -> Optional[int]:
    """Persist a finished investigation. Returns the new row id, or None if
    persistence is disabled/unavailable."""
    if not engine_available():
        return None
    try:
        with session_scope() as s:
            row = Investigation(trigger=trigger, service=service, metric=metric)
            _apply_result(row, ledger)
            s.add(row)
            s.flush()  # assign the primary key so we can return it
            return row.id
    except Exception as exc:
        log.warning("save_completed failed: %s", exc)
        return None


# --- asynchronous path (used by /alert + the Celery worker) -----------------
def create_pending(
    *,
    trigger: str = "alert",
    service: Optional[str] = None,
    metric: Optional[str] = None,
    incident_id: Optional[str] = None,
) -> Optional[int]:
    """Insert a 'queued' row before the job is enqueued; returns its id."""
    if not engine_available():
        return None
    try:
        with session_scope() as s:
            row = Investigation(
                trigger=trigger,
                service=service,
                metric=metric,
                incident_id=incident_id,
                status="queued",
            )
            s.add(row)
            s.flush()
            return row.id
    except Exception as exc:
        log.warning("create_pending failed: %s", exc)
        return None


def attach_job_id(row_id: Optional[int], job_id: str) -> None:
    """Record the Celery task id on the row once it has been enqueued."""
    if not engine_available() or row_id is None:
        return
    try:
        with session_scope() as s:
            row = s.get(Investigation, row_id)
            if row:
                row.job_id = job_id
    except Exception as exc:
        log.warning("attach_job_id failed: %s", exc)


def mark_running(row_id: Optional[int]) -> None:
    if not engine_available() or row_id is None:
        return
    try:
        with session_scope() as s:
            row = s.get(Investigation, row_id)
            if row:
                row.status = "running"
    except Exception as exc:
        log.warning("mark_running failed: %s", exc)


def mark_done(row_id: Optional[int], ledger: EvidenceLedger) -> None:
    if not engine_available() or row_id is None:
        return
    try:
        with session_scope() as s:
            row = s.get(Investigation, row_id)
            if row:
                _apply_result(row, ledger)
    except Exception as exc:
        log.warning("mark_done failed: %s", exc)


def mark_failed(row_id: Optional[int], error: str) -> None:
    if not engine_available() or row_id is None:
        return
    try:
        with session_scope() as s:
            row = s.get(Investigation, row_id)
            if row:
                row.status = "failed"
                row.error = error[:5000]
    except Exception as exc:
        log.warning("mark_failed failed: %s", exc)


# --- reads (history views) --------------------------------------------------
def list_investigations(limit: int = 50) -> list[dict]:
    if not engine_available():
        return []
    try:
        with session_scope() as s:
            rows = (
                s.execute(select(Investigation).order_by(Investigation.id.desc()).limit(limit))
                .scalars()
                .all()
            )
            return [r.summary_dict() for r in rows]
    except Exception as exc:
        log.warning("list_investigations failed: %s", exc)
        return []


def get_investigation(row_id: int) -> Optional[dict]:
    if not engine_available():
        return None
    try:
        with session_scope() as s:
            row = s.get(Investigation, row_id)
            return row.detail_dict() if row else None
    except Exception as exc:
        log.warning("get_investigation failed: %s", exc)
        return None
