"""
HTTP helpers for the Streamlit cockpit.

Pure functions (no Streamlit import) so they can be tested against the live
stack. The cockpit talks to every running service over HTTP:
  * the dummy target  (faults, metrics source, logs, deploys)
  * Sentinel AI        (runs the investigation via /alert)
  * Prometheus         (live metric values + range charts)

URLs default to the published localhost ports; override via env for a
containerized UI.
"""

from __future__ import annotations

import os
import time

import requests

# Internal URLs the cockpit calls server-side. Default to localhost for a local
# `streamlit run`; in docker-compose these are the service names.
DUMMY_URL = os.environ.get("DUMMY_URL", "http://localhost:9000")
SENTINEL_URL = os.environ.get("SENTINEL_URL", "http://localhost:8000")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

# Browser-facing URLs for the clickable links shown in the UI. Default to the
# internal URLs so local runs need no extra config; in compose these point at
# the published localhost ports (a browser can't resolve compose service names).
DUMMY_PUBLIC_URL = os.environ.get("DUMMY_PUBLIC_URL", DUMMY_URL)
SENTINEL_PUBLIC_URL = os.environ.get("SENTINEL_PUBLIC_URL", SENTINEL_URL)
PROMETHEUS_PUBLIC_URL = os.environ.get("PROMETHEUS_PUBLIC_URL", PROMETHEUS_URL)

SERVICE = os.environ.get("TARGET_SERVICE", "svc-checkout")

# PromQL for the live-telemetry tiles/charts (kept in sync with the ingestion adapter).
Q_ERROR_RATE = (
    f'100 * (sum(rate(http_requests_total{{service="{SERVICE}",status=~"5..|429"}}[1m]))'
    f' / sum(rate(http_requests_total{{service="{SERVICE}"}}[1m])))'
)
Q_LATENCY_P95 = (
    f'1000 * histogram_quantile(0.95,'
    f' sum(rate(http_request_duration_seconds_bucket{{service="{SERVICE}"}}[1m])) by (le))'
)
Q_RPS = f'sum(rate(http_requests_total{{service="{SERVICE}"}}[1m]))'
Q_CPU = f'cpu_usage_ratio{{service="{SERVICE}"}}'
Q_CACHE = f'cache_hit_ratio{{service="{SERVICE}"}}'
Q_DB_POOL = f'db_pool_active_connections{{service="{SERVICE}"}}'


# --- service reachability ---
def _reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        return requests.get(url, timeout=timeout).status_code < 500
    except requests.RequestException:
        return False


def service_status() -> dict[str, tuple[bool, str]]:
    """{name: (is_up, browser_link)} — reachability checked over the internal
    URL, but the link shown to the user is the browser-facing public URL."""
    return {
        "Dummy target": (_reachable(f"{DUMMY_URL}/healthz"), f"{DUMMY_PUBLIC_URL}/docs"),
        "Sentinel API": (_reachable(f"{SENTINEL_URL}/health"), f"{SENTINEL_PUBLIC_URL}/docs"),
        "Prometheus": (_reachable(f"{PROMETHEUS_URL}/-/healthy"), PROMETHEUS_PUBLIC_URL),
    }


# --- dummy target ---
def get_faults() -> dict:
    r = requests.get(f"{DUMMY_URL}/faults", timeout=5)
    r.raise_for_status()
    return r.json()


def inject_fault(fault_id: str) -> dict:
    r = requests.post(f"{DUMMY_URL}/faults/{fault_id}", timeout=5)
    r.raise_for_status()
    return r.json()


def clear_fault() -> dict:
    r = requests.post(f"{DUMMY_URL}/faults/clear", timeout=5)
    r.raise_for_status()
    return r.json()


def get_logs(limit: int = 15) -> list[dict]:
    r = requests.get(f"{DUMMY_URL}/logs", params={"limit": limit}, timeout=5)
    r.raise_for_status()
    return r.json()["logs"]


def get_deploys() -> list[dict]:
    r = requests.get(f"{DUMMY_URL}/deploys", timeout=5)
    r.raise_for_status()
    return r.json()["deploys"]


# --- Prometheus ---
def prom_instant(query: str) -> float | None:
    r = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=10)
    r.raise_for_status()
    result = r.json()["data"]["result"]
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (ValueError, TypeError):
        return None


def prom_range(query: str, minutes: int = 10, step: int = 20) -> list[float]:
    end = time.time()
    start = end - minutes * 60
    r = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query_range",
        params={"query": query, "start": start, "end": end, "step": step},
        timeout=15,
    )
    r.raise_for_status()
    result = r.json()["data"]["result"]
    if not result:
        return []
    out: list[float] = []
    for _, raw in result[0]["values"]:
        try:
            v = float(raw)
        except (ValueError, TypeError):
            continue
        if v == v:  # drop NaN
            out.append(v)
    return out


# --- Sentinel AI ---
def run_alert(service: str, metric: str, window_minutes: int) -> dict:
    """Fire an alert at Sentinel. Returns the raw /alert response, which is
    either an async handle ({investigation_id, status: 'queued'}) when the queue
    is configured, or the full synchronous result in a DB-less dev setup."""
    r = requests.post(
        f"{SENTINEL_URL}/alert",
        json={"service": service, "metric": metric, "window_minutes": window_minutes},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_investigation(investigation_id: int) -> dict:
    r = requests.get(f"{SENTINEL_URL}/investigations/{investigation_id}", timeout=10)
    r.raise_for_status()
    return r.json()


def poll_investigation(investigation_id: int, timeout: int = 180, interval: float = 2.0) -> dict:
    """Poll the job until it is done/failed (the Celery worker runs it ~20s).
    Returns the final record; if the timeout is hit, returns the last read
    (its status will still be queued/running)."""
    deadline = time.time() + timeout
    record = get_investigation(investigation_id)
    while time.time() < deadline:
        if record.get("status") in ("done", "failed"):
            return record
        time.sleep(interval)
        record = get_investigation(investigation_id)
    return record


def list_investigations(limit: int = 20) -> list[dict]:
    r = requests.get(f"{SENTINEL_URL}/investigations", params={"limit": limit}, timeout=10)
    r.raise_for_status()
    return r.json().get("investigations", [])
