"""Live telemetry -> IncidentScenario.

The front door that turns Sentinel from "runs on fixture JSON" into
"investigates a live system." Given an alert (service + which metric fired +
a time window) it:

  1. runs a PromQL range query for that metric (a baseline->spike series the
     correlator can analyze),
  2. pulls recent logs + deploy history from the target,
  3. packs them into the same IncidentScenario the agents already speak — with
     no ground-truth fields, since a real incident has none.

Everything downstream is unchanged; only this front door is new. PromQL lives
here so the rest of the system stays telemetry-agnostic.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from app.schemas.scenario import CommitInfo, IncidentScenario, LogEntry, MetricPoint

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
# The target service exposing /logs and /deploys (a stand-in for Loki + a
# deploy registry in this self-contained demo).
TARGET_URL = os.environ.get("TARGET_URL", "http://localhost:9000")

# Friendly metric key -> PromQL. error_rate/latency/rps are derived from the
# raw counters/histogram; the rest are gauges read directly.
_METRIC_TEMPLATES: dict[str, str] = {
    "error_rate_pct": '100 * (sum(rate(http_requests_total{{service="{s}",status=~"5..|429"}}[1m]))'
                      ' / sum(rate(http_requests_total{{service="{s}"}}[1m])))',
    "latency_p95_ms": '1000 * histogram_quantile(0.95,'
                      ' sum(rate(http_request_duration_seconds_bucket{{service="{s}"}}[1m])) by (le))',
    "requests_per_second": 'sum(rate(http_requests_total{{service="{s}"}}[1m]))',
    "cpu_usage_ratio": 'cpu_usage_ratio{{service="{s}"}}',
    "db_pool_active_connections": 'db_pool_active_connections{{service="{s}"}}',
    "cache_hit_ratio": 'cache_hit_ratio{{service="{s}"}}',
    "process_memory_working_set_bytes": 'process_memory_working_set_bytes{{service="{s}"}}',
    "disk_used_ratio": 'disk_used_ratio{{service="{s}"}}',
}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _promql_range(query: str, start: float, end: float, step: int = 20) -> list[tuple[float, float]]:
    """Run a Prometheus range query; return [(unix_ts, value)] with NaN/inf dropped."""
    resp = httpx.get(
        f"{PROMETHEUS_URL}/api/v1/query_range",
        params={"query": query, "start": start, "end": end, "step": step},
        timeout=15.0,
    )
    resp.raise_for_status()
    result = resp.json()["data"]["result"]
    if not result:
        return []
    points: list[tuple[float, float]] = []
    for ts, raw in result[0]["values"]:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
            continue
        points.append((float(ts), v))
    return points


def _fetch_metric(service: str, metric: str, start: float, end: float) -> list[MetricPoint]:
    if metric not in _METRIC_TEMPLATES:
        raise ValueError(f"unknown metric '{metric}'. Known: {sorted(_METRIC_TEMPLATES)}")
    query = _METRIC_TEMPLATES[metric].format(s=service)
    return [
        MetricPoint(timestamp=_iso(ts), metric_name=metric, value=value, service=service)
        for ts, value in _promql_range(query, start, end)
    ]


def _fetch_logs(service: str, start_iso: str, max_logs: int = 10) -> list[LogEntry]:
    """Recent structured logs in the window, deduped by message (newest kept)."""
    resp = httpx.get(f"{TARGET_URL}/logs", params={"since": start_iso, "limit": 500}, timeout=15.0)
    resp.raise_for_status()
    raw = resp.json()["logs"]
    by_message: dict[str, dict] = {}
    for e in raw:
        by_message[e["message"]] = e  # later (newer) entries overwrite -> newest kept
    deduped = sorted(by_message.values(), key=lambda e: e["timestamp"])[-max_logs:]
    return [
        LogEntry(
            timestamp=e["timestamp"], service=e["service"], level=e["level"],
            message=e["message"], line_id=e["line_id"],
        )
        for e in deduped
    ]


def _fetch_deploys() -> list[CommitInfo]:
    resp = httpx.get(f"{TARGET_URL}/deploys", timeout=15.0)
    resp.raise_for_status()
    return [
        CommitInfo(
            sha=d["sha"], author=d["author"], timestamp=d["timestamp"],
            message=d["message"], files_changed=d["files_changed"],
            is_guilty_commit=d.get("is_guilty_commit", False),
        )
        for d in resp.json()["deploys"]
    ]


def build_incident_from_alert(
    service: str,
    metric: str = "error_rate_pct",
    window_minutes: int = 10,
    now: float | None = None,
) -> IncidentScenario:
    """Assemble a real, un-labelled IncidentScenario from live telemetry.

    `now` (unix seconds) is injectable for testing; defaults to wall clock.
    """
    import time

    end = now if now is not None else time.time()
    start = end - window_minutes * 60
    start_iso = _iso(start)

    metrics = _fetch_metric(service, metric, start, end)
    logs = _fetch_logs(service, start_iso)
    deploys = _fetch_deploys()

    return IncidentScenario(
        scenario_id=f"live-{service}-{int(end)}",
        title=f"{service}: {metric} alert",
        services_affected=[service],
        metrics=metrics,
        logs=logs,
        deploy_history=deploys,
    )
