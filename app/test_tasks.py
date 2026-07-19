"""Celery task tests in eager mode — no Redis broker, no live telemetry.

`task_always_eager` runs the task inline in the test process, so we can verify
the task's persistence wiring (queued -> running -> done | failed) without a
broker. The telemetry adapter and the LLM pipeline are stubbed so the test is
fast and deterministic; their own behaviour is covered elsewhere.
"""

from __future__ import annotations

import pytest

from app.schemas.evidence import EvidenceLedger, EvidenceObject, Hypothesis, SourceType
from app.schemas.scenario import IncidentScenario
from app.agents.postmortem import generate_postmortem


def _fake_scenario(service: str, metric: str, window_minutes: int) -> IncidentScenario:
    return IncidentScenario(
        scenario_id="live",
        title="live incident",
        services_affected=[service],
        metrics=[],
        logs=[],
        deploy_history=[],
    )


def _fake_ledger(_scenario) -> EvidenceLedger:
    ref = "prometheus:error_rate_pct:svc-checkout:2026-07-19T10:00:00Z"
    ledger = EvidenceLedger(incident_id="live_inc")
    ledger.add_evidence(
        EvidenceObject(
            claim="Error rate elevated",
            source_type=SourceType.METRIC,
            source_ref=ref,
            confidence=0.9,
            produced_by="correlator_agent",
        )
    )
    ledger.hypotheses.append(
        Hypothesis(
            hypothesis_id="h1",
            description="Downstream dependency slowdown",
            confidence=0.77,
            supporting_evidence_refs=[ref],
            status="survived_critique",
        )
    )
    generate_postmortem(ledger, title="live incident")
    return ledger


@pytest.fixture()
def eager(tmp_path, monkeypatch):
    """SQLite persistence + Celery running tasks inline."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'tasks_test.db'}")

    from app.db import base

    base.reset_engine_for_tests()
    base.init_db()

    from app.tasks import celery_app

    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)

    from app.db import repository

    yield repository
    base.reset_engine_for_tests()


def test_task_success_marks_done(eager, monkeypatch):
    import app.ingestion.adapter as adapter
    import app.pipeline as pipeline
    from app.tasks import run_investigation_task

    monkeypatch.setattr(adapter, "build_incident_from_alert", _fake_scenario)
    monkeypatch.setattr(pipeline, "run_investigation", _fake_ledger)

    row_id = eager.create_pending(trigger="alert", service="svc-checkout", metric="error_rate_pct")
    run_investigation_task.delay(row_id, "svc-checkout", "error_rate_pct", 8).get()

    rec = eager.get_investigation(row_id)
    assert rec["status"] == "done"
    assert rec["top_root_cause"] == "Downstream dependency slowdown"
    assert rec["evidence_count"] == 1
    assert "# live incident" in rec["postmortem_markdown"]


def test_task_failure_marks_failed(eager, monkeypatch):
    import app.ingestion.adapter as adapter
    from app.tasks import run_investigation_task

    def _boom(*_a, **_k):
        raise RuntimeError("Prometheus unreachable")

    monkeypatch.setattr(adapter, "build_incident_from_alert", _boom)

    row_id = eager.create_pending(trigger="alert", service="svc-checkout", metric="error_rate_pct")
    with pytest.raises(RuntimeError):
        run_investigation_task.delay(row_id, "svc-checkout", "error_rate_pct", 8).get()

    rec = eager.get_investigation(row_id)
    assert rec["status"] == "failed"
    assert "Prometheus unreachable" in rec["error"]
