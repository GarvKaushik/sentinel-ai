"""Repository tests that run against a throwaway SQLite file (no Postgres/Docker).

The persistence layer is DB-agnostic (generic SQLAlchemy types), so exercising
it on SQLite verifies the model, the session handling, and every repository
function locally. In production the exact same code runs on Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.evidence import (
    EvidenceLedger,
    EvidenceObject,
    Hypothesis,
    Recommendation,
    SourceType,
)
from app.agents.postmortem import generate_postmortem


def _make_ledger() -> EvidenceLedger:
    ref = "prometheus:error_rate_pct:svc-checkout:2026-07-19T10:00:00Z"
    ledger = EvidenceLedger(incident_id="test_incident")
    ledger.add_evidence(
        EvidenceObject(
            claim="Error rate jumped to 12% at 10:00 UTC",
            source_type=SourceType.METRIC,
            source_ref=ref,
            confidence=0.95,
            produced_by="correlator_agent",
            timestamp=datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc),
        )
    )
    ledger.hypotheses.append(
        Hypothesis(
            hypothesis_id="h1",
            description="Bad deploy introduced a regression",
            confidence=0.82,
            supporting_evidence_refs=[ref],
            status="survived_critique",
        )
    )
    ledger.recommendation = Recommendation(
        summary="Roll back the latest deploy",
        detailed_steps=["Revert the guilty commit", "Redeploy"],
        supporting_evidence_refs=[ref],
    )
    generate_postmortem(ledger, title="Test incident")
    return ledger


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A repository bound to a fresh SQLite file for this test only."""
    db_file = tmp_path / "sentinel_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")

    from app.db import base

    base.reset_engine_for_tests()
    assert base.init_db() is True

    from app.db import repository

    yield repository
    base.reset_engine_for_tests()


def test_save_completed_then_read_back(repo):
    ledger = _make_ledger()
    row_id = repo.save_completed(ledger, trigger="manual", service="svc-checkout", metric="error_rate_pct")
    assert isinstance(row_id, int)

    listed = repo.list_investigations()
    assert len(listed) == 1
    assert listed[0]["id"] == row_id
    assert listed[0]["status"] == "done"
    assert listed[0]["top_root_cause"] == "Bad deploy introduced a regression"
    assert listed[0]["confidence"] == pytest.approx(0.82)

    detail = repo.get_investigation(row_id)
    assert detail["incident_id"] == "test_incident"
    assert detail["ledger"]["incident_id"] == "test_incident"
    assert "# Test incident" in detail["postmortem_markdown"]


def test_async_lifecycle(repo):
    row_id = repo.create_pending(trigger="alert", service="svc-checkout", metric="error_rate_pct")
    assert isinstance(row_id, int)
    assert repo.get_investigation(row_id)["status"] == "queued"

    repo.attach_job_id(row_id, "celery-task-abc")
    repo.mark_running(row_id)
    assert repo.get_investigation(row_id)["status"] == "running"

    repo.mark_done(row_id, _make_ledger())
    done = repo.get_investigation(row_id)
    assert done["status"] == "done"
    assert done["job_id"] == "celery-task-abc"
    assert done["evidence_count"] == 1


def test_mark_failed(repo):
    row_id = repo.create_pending(trigger="alert", service="svc-checkout")
    repo.mark_failed(row_id, "boom: ingestion adapter could not reach Prometheus")
    failed = repo.get_investigation(row_id)
    assert failed["status"] == "failed"
    assert "boom" in failed["error"]


def test_disabled_when_no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app.db import base, repository

    base.reset_engine_for_tests()
    assert base.engine_available() is False
    assert repository.save_completed(_make_ledger()) is None
    assert repository.list_investigations() == []
    assert repository.get_investigation(1) is None
