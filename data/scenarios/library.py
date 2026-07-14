"""Hand-audited synthetic incidents used by local evaluation.

The catalogue covers distinct failure modes and intentionally includes
non-deployment incidents and timing decoys. Every expected reference is a
claim the deterministic correlator should discover before an LLM reasons over
the evidence.
"""

from __future__ import annotations

from app.schemas.scenario import CommitInfo, IncidentScenario, LogEntry, MetricPoint
from data.scenarios.scenario_001_bad_deploy import scenario_001


def _series(service: str, metric: str, date: str, normal: list[float], incident: list[float]) -> list[MetricPoint]:
    return [
        MetricPoint(
            timestamp=f"{date}T14:{30 + index:02d}:00Z",
            metric_name=metric,
            value=value,
            service=service,
        )
        for index, value in enumerate(normal + incident)
    ]


def _scenario(
    *, scenario_id: str, title: str, service: str, category: str, cause: str,
    metric: str, date: str, normal: list[float], incident: list[float],
    log_message: str | None, log_id: str | None, commits: list[CommitInfo] | None = None,
    red_herrings: list[str] | None = None,
) -> IncidentScenario:
    logs = []
    expected = [f"prometheus:{metric}:{service}:{date}T14:33:00Z"]
    if log_message and log_id:
        logs.append(LogEntry(
            timestamp=f"{date}T14:33:05Z", service=service, level="ERROR",
            message=log_message, line_id=log_id,
        ))
        expected.append(log_id)
    for commit in commits or []:
        if commit.is_guilty_commit:
            expected.append(f"commit:{commit.sha}")
    return IncidentScenario(
        scenario_id=scenario_id, title=title, services_affected=[service],
        injected_root_cause=cause, root_cause_category=category,
        metrics=_series(service, metric, date, normal, incident), logs=logs,
        deploy_history=commits or [], red_herrings=red_herrings or [],
        expected_evidence_refs=expected,
    )


scenario_002_db_pool = _scenario(
    scenario_id="scenario_002_db_connection_pool",
    title="Orders API times out from database connection pool exhaustion",
    service="svc-orders", category="db_connection_pool",
    cause="A slow order-history query held database connections until the orders pool was exhausted.",
    metric="db_pool_active_connections", date="2026-07-07", normal=[12, 13, 12], incident=[50, 50, 50],
    log_message="DatabasePoolTimeoutException: timed out waiting for a connection from orders-primary pool",
    log_id="log:svc-orders:line:1201",
    commits=[CommitInfo(
        sha="b2c3d4e", author="alex", timestamp="2026-07-07T14:31:30Z",
        message="Add order history filter query", files_changed=["services/orders/repository/OrderHistoryRepository.java"],
        is_guilty_commit=True,
    )],
)

scenario_003_dependency_timeout = _scenario(
    scenario_id="scenario_003_dependency_timeout", title="Checkout errors after payments dependency timeout",
    service="svc-checkout", category="dependency_timeout",
    cause="The payments dependency became unavailable, causing checkout requests to time out; no checkout deploy caused the incident.",
    metric="error_rate_pct", date="2026-07-08", normal=[0.2, 0.3, 0.2], incident=[8.4, 16.7, 18.1],
    log_message="UpstreamTimeout: POST payments-authorize exceeded configured 2000ms deadline",
    log_id="log:svc-checkout:line:822",
)

scenario_004_config_drift = _scenario(
    scenario_id="scenario_004_config_drift", title="Catalog requests fail after configuration drift",
    service="svc-catalog", category="config_drift",
    cause="The catalog production feature-flag configuration drifted and disabled a required pricing provider setting outside the deploy workflow.",
    metric="error_rate_pct", date="2026-07-09", normal=[0.1, 0.1, 0.2], incident=[6.0, 9.5, 10.2],
    log_message="ConfigurationError: PRICING_PROVIDER_URL is unset for enabled dynamic-pricing path",
    log_id="log:svc-catalog:line:117",
)

scenario_005_resource_exhaustion = _scenario(
    scenario_id="scenario_005_resource_exhaustion", title="Image service fails under memory pressure",
    service="svc-images", category="resource_exhaustion",
    cause="An image-processing memory leak exhausted pod memory and caused repeated OOM kills.",
    metric="memory_working_set_mb", date="2026-07-10", normal=[420, 435, 441], incident=[1800, 1940, 2048],
    log_message="OOMKilled: container image-resizer exceeded its 2048Mi memory limit",
    log_id="log:svc-images:line:501",
)

scenario_006_traffic_spike = _scenario(
    scenario_id="scenario_006_traffic_spike", title="Search latency rises during legitimate traffic surge",
    service="svc-search", category="traffic_spike_no_bug",
    cause="A legitimate traffic surge exceeded search service capacity; there was no faulty deployment.",
    metric="requests_per_second", date="2026-07-11", normal=[180, 190, 185], incident=[1200, 1450, 1510],
    log_message=None, log_id=None,
)

scenario_007_partial_rollback = _scenario(
    scenario_id="scenario_007_partial_rollback", title="Inventory errors continue after partial rollback",
    service="svc-inventory", category="partial_rollback",
    cause="A rollback left a mixed inventory deployment, producing an API contract mismatch between old and new pods.",
    metric="error_rate_pct", date="2026-07-12", normal=[0.2, 0.3, 0.2], incident=[7.9, 11.5, 13.3],
    log_message="ContractMismatchException: inventory response schema v2 cannot be read by v1 consumer",
    log_id="log:svc-inventory:line:991",
    commits=[
        CommitInfo(
            sha="c3d4e5f", author="maya", timestamp="2026-07-12T14:31:45Z",
            message="Rollback inventory API after v2 deployment", files_changed=["services/inventory/api/InventoryResponse.java"],
            is_guilty_commit=True,
        ),
        CommitInfo(
            sha="d4e5f6a", author="lee", timestamp="2026-07-12T14:30:30Z",
            message="Refresh notification email copy", files_changed=["services/notifications/templates/stock_email.html"],
            is_guilty_commit=False,
        ),
    ],
    red_herrings=["Commit d4e5f6a is nearby in time but belongs to svc-notifications, not the failing inventory service."],
)


def all_scenarios() -> list[IncidentScenario]:
    return [
        scenario_001, scenario_002_db_pool, scenario_003_dependency_timeout,
        scenario_004_config_drift, scenario_005_resource_exhaustion,
        scenario_006_traffic_spike, scenario_007_partial_rollback,
    ]
