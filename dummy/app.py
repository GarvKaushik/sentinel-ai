"""
Dummy target service for Sentinel AI demos.

A stand-in for "a real production microservice." It:
  * exposes Prometheus metrics at /metrics,
  * runs a background simulator that emits realistic baseline traffic and,
    when a fault is injected, shifts exactly the metric + log signature that
    fault's runbook describes,
  * serves structured logs at /logs and a deploy history at /deploys — the two
    non-metric sources Sentinel's ingestion adapter will read,
  * takes fault injection via POST /faults/{fault_id}.

Sentinel does NOT run inside this app. This is the *subject*; Sentinel observes
it from outside (Prometheus scrape + /logs + /deploys). Run:

    uvicorn dummy.app:app --port 9000
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from dummy.faults import BASELINE, FAULTS, profile_for

SERVICE = os.environ.get("SERVICE_NAME", "svc-checkout")
BASE_RPS = 20
TICK_SECONDS = 1.0

# --- Prometheus metrics (scraped by Prometheus, queried by Sentinel) ---
REQS = Counter("http_requests_total", "HTTP requests", ["service", "status"])
LAT = Histogram(
    "http_request_duration_seconds", "Request latency", ["service"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
DB_ACTIVE = Gauge("db_pool_active_connections", "Active DB connections", ["service"])
DB_MAX = Gauge("db_pool_max_connections", "DB pool size", ["service"])
MEM = Gauge("process_memory_working_set_bytes", "Working set memory", ["service"])
CPU = Gauge("cpu_usage_ratio", "CPU utilization 0-1", ["service"])
DISK = Gauge("disk_used_ratio", "Disk utilization 0-1", ["service"])
CACHE = Gauge("cache_hit_ratio", "Cache hit ratio 0-1", ["service"])

# --- runtime state ---
STATE: dict = {
    "active_fault": None,
    "mem_mb": BASELINE["memory_mb"],   # ramp tracker for memory_leak
    "disk": BASELINE["disk"],          # ramp tracker for disk_full
    "line": 0,                         # log line counter
}
LOGS: deque[dict] = deque(maxlen=5000)
DEPLOYS: list[dict] = []


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_log(service: str, level: str, message: str) -> None:
    STATE["line"] += 1
    entry = {
        "timestamp": now_iso(),
        "service": service,
        "level": level,
        "message": message,
        "line_id": f"log:{service}:line:{STATE['line']}",
    }
    LOGS.append(entry)
    print(json.dumps(entry), flush=True)


def add_deploy(fault_id: str) -> None:
    dep = FAULTS[fault_id].get("deploy")
    if not dep:
        return
    DEPLOYS.append({
        "sha": uuid.uuid4().hex[:7],
        "author": "dummy-ci",
        "timestamp": now_iso(),
        "message": dep["message"],
        "files_changed": dep["files_changed"],
        "is_guilty_commit": dep.get("is_guilty_commit", True),
    })


def _sample_latency_seconds(p95_ms: float) -> float:
    """Draw a latency whose 95th percentile is ~p95_ms: most requests fast,
    ~5% near the target so histogram_quantile(0.95, ...) lands on p95_ms."""
    if random.random() < 0.95:
        return random.uniform(0.005, 0.035)
    return random.uniform(p95_ms * 0.8, p95_ms * 1.2) / 1000.0


def simulate_tick() -> None:
    """One second of simulated traffic + resource state for the active fault."""
    fault = STATE["active_fault"]
    p = profile_for(fault)
    svc = SERVICE

    # Requests this tick, with the fault's error fraction and status code.
    n = max(1, int(BASE_RPS * p["rps_multiplier"]))
    errors = int(round(n * p["error_rate"]))
    for i in range(n):
        status = p["error_status"] if i < errors else 200
        REQS.labels(svc, str(status)).inc()
        LAT.labels(svc).observe(_sample_latency_seconds(p["latency_p95_ms"]))

    # Memory: ramp upward under memory_leak, sawtooth-reset on OOM.
    if p.get("ramp") == "memory":
        STATE["mem_mb"] += 180
        if STATE["mem_mb"] > 2048:
            emit_log(svc, "ERROR", FAULTS["memory_leak"]["log"])  # OOMKilled
            STATE["mem_mb"] = BASELINE["memory_mb"]
        mem_mb = STATE["mem_mb"]
    else:
        STATE["mem_mb"] = p["memory_mb"]
        mem_mb = p["memory_mb"]

    # Disk: fill over time under disk_full, plateau near full.
    if p.get("ramp") == "disk":
        STATE["disk"] = min(0.99, STATE["disk"] + 0.03)
        disk = STATE["disk"]
    else:
        STATE["disk"] = p["disk"]
        disk = p["disk"]

    MEM.labels(svc).set(mem_mb * 1_000_000)
    CPU.labels(svc).set(p["cpu"])
    DISK.labels(svc).set(disk)
    CACHE.labels(svc).set(p["cache_hit"])
    DB_ACTIVE.labels(svc).set(p["db_pool_active"])
    DB_MAX.labels(svc).set(p["db_pool_max"])

    # Emit the fault's log line (memory_leak logs only on the OOM reset above).
    if fault and p.get("log") and p.get("ramp") != "memory":
        emit_log(svc, p.get("log_level", "ERROR"), p["log"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def simulator():
        while True:
            try:
                simulate_tick()
            except Exception as e:  # keep the loop alive no matter what
                print(f"simulator error: {e}", flush=True)
            await asyncio.sleep(TICK_SECONDS)

    task = asyncio.create_task(simulator())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title=f"Dummy Target Service ({SERVICE})", version="0.1.0", lifespan=lifespan)


@app.get("/metrics")
def metrics():
    """Prometheus exposition. An explicit route (not a sub-app mount) so the
    path has no trailing-slash redirect for the scraper."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
def root():
    return {"service": SERVICE, "active_fault": STATE["active_fault"], "available_faults": sorted(FAULTS)}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/faults")
def list_faults():
    return {
        "active": STATE["active_fault"],
        "available": {fid: FAULTS[fid]["category"] for fid in sorted(FAULTS)},
    }


# NOTE: the static /faults/clear route MUST be declared before the
# /faults/{fault_id} route — FastAPI matches in definition order, so otherwise
# "clear" is captured as a fault_id.
@app.post("/faults/clear")
def clear_fault():
    STATE["active_fault"] = None
    STATE["mem_mb"] = BASELINE["memory_mb"]
    STATE["disk"] = BASELINE["disk"]
    return {"active_fault": None}


@app.post("/faults/{fault_id}")
def inject_fault(fault_id: str):
    if fault_id not in FAULTS:
        raise HTTPException(status_code=404, detail=f"unknown fault '{fault_id}'. Known: {sorted(FAULTS)}")
    STATE["active_fault"] = fault_id
    add_deploy(fault_id)
    return {"active_fault": fault_id, "category": FAULTS[fault_id]["category"], "runbook": FAULTS[fault_id]["runbook"]}


@app.get("/logs")
def get_logs(level: str | None = None, since: str | None = None, limit: int = 500):
    """Recent structured logs. `since` is an ISO timestamp lower bound."""
    items = list(LOGS)
    if level:
        items = [e for e in items if e["level"] == level.upper()]
    if since:
        items = [e for e in items if e["timestamp"] >= since]
    return {"logs": items[-limit:]}


@app.get("/deploys")
def get_deploys():
    return {"deploys": DEPLOYS}
