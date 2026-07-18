"""
Fault catalogue for the dummy target service.

Each entry describes how one manufactured failure mode shifts the service's
telemetry, so the background simulator in app.py can reproduce the metric +
log signature that Sentinel AI's runbooks describe. This is the single source
of truth shared with data/runbooks/ — keep the keys in sync with those files.

A profile only lists what CHANGES from BASELINE; everything else stays healthy.
"""

from __future__ import annotations

# Healthy steady-state the simulator emits when no fault is active.
BASELINE = {
    "error_rate": 0.003,      # fraction of requests that fail
    "error_status": 500,      # status code used for failures
    "latency_p95_ms": 50,     # 95th-percentile request latency
    "rps_multiplier": 1.0,    # multiplier on the ~20 req/s baseline
    "cpu": 0.20,              # cpu_usage_ratio (0-1)
    "memory_mb": 400,         # working set
    "disk": 0.40,            # disk_used_ratio (0-1)
    "cache_hit": 0.95,       # cache_hit_ratio (0-1)
    "db_pool_active": 10,    # active db connections
    "db_pool_max": 50,       # pool size
}

# fault_id -> profile. "runbook" is the matching data/runbooks/ doc stem.
# "deploy" (if present) is appended to the deploy log when the fault activates,
# so deploy-linked incidents produce real commit evidence. "ramp" marks a
# gauge that should climb over time rather than step-change.
FAULTS: dict[str, dict] = {
    "bad_deploy": {
        "category": "bad_deploy",
        "runbook": "runbook-deploy-error-spike",
        "error_rate": 0.35,
        "log": 'NullPointerException at Validator.validate(Validator.java:142): '
               'Cannot invoke "BillingAddress.getPostalCode()" because "customer.billing_address" is null',
        "deploy": {
            "message": "Refactor Validator: simplify address validation logic",
            "files_changed": ["src/checkout/Validator.java"],
            "is_guilty_commit": True,
        },
    },
    "db_pool_exhaustion": {
        "category": "db_connection_pool",
        "runbook": "runbook-db-connection-pool",
        "error_rate": 0.08,
        "latency_p95_ms": 1500,
        "db_pool_active": 50,  # == max, pool exhausted
        "log": "DatabasePoolTimeoutException: timed out waiting for a connection from the primary pool",
    },
    "downstream_slowdown": {
        "category": "api_latency_spike",
        "runbook": "runbook-api-latency-spike",
        "latency_p95_ms": 1200,  # latency up, error rate stays at baseline
        "log": "WARN slow downstream response: pricing-service call took 1180ms",
        "log_level": "WARN",
    },
    "dependency_timeout": {
        "category": "dependency_timeout",
        "runbook": "runbook-dependency-timeout",
        "error_rate": 0.20,
        "error_status": 504,
        "latency_p95_ms": 2000,  # requests pile up at the client timeout
        "log": "UpstreamTimeout: POST payments-authorize exceeded configured 2000ms deadline",
    },
    "config_drift": {
        "category": "config_drift",
        "runbook": "runbook-config-drift",
        "error_rate": 0.10,
        "log": "ConfigurationError: PRICING_PROVIDER_URL is unset for enabled dynamic-pricing path",
        # no "deploy" — config drift happens WITHOUT a code change
    },
    "memory_leak": {
        "category": "resource_exhaustion",
        "runbook": "runbook-memory-leak-oom",
        "ramp": "memory",        # working set climbs each tick until OOM, then resets
        "error_rate": 0.05,
        "log": "OOMKilled: container exceeded its 2048Mi memory limit",
    },
    "cpu_saturation": {
        "category": "resource_exhaustion",
        "runbook": "runbook-cpu-saturation",
        "cpu": 0.98,
        "latency_p95_ms": 900,  # slow, but errors stay low
        "log": "WARN event loop lag 850ms under CPU pressure",
        "log_level": "WARN",
    },
    "traffic_spike": {
        "category": "traffic_spike_no_bug",
        "runbook": "runbook-traffic-spike",
        "rps_multiplier": 8.0,   # big load rise...
        "latency_p95_ms": 600,   # ...latency rises...
        # ...but error_rate stays at baseline — this is the "no bug" control
    },
    "partial_rollback": {
        "category": "partial_rollback",
        "runbook": "runbook-partial-rollback",
        "error_rate": 0.15,      # intermittent — only mixed-version calls fail
        "log": "ContractMismatchException: response schema v2 cannot be read by v1 consumer",
        "deploy": {
            "message": "Rollback checkout API after failed v2 rollout",
            "files_changed": ["src/checkout/api/CheckoutResponse.java"],
            "is_guilty_commit": True,
        },
    },
    "rate_limit": {
        "category": "rate_limit_exhaustion",
        "runbook": "runbook-rate-limit-exhaustion",
        "error_rate": 0.18,
        "error_status": 429,
        "log": "429 Too Many Requests from geocoding-api: rate limit exceeded",
    },
    "disk_full": {
        "category": "disk_space_exhaustion",
        "runbook": "runbook-disk-space-exhaustion",
        "ramp": "disk",          # disk fills over time and plateaus near full
        "error_rate": 0.12,
        "log": "IOError: No space left on device while writing upload buffer",
    },
    "cache_outage": {
        "category": "cache_outage",
        "runbook": "runbook-cache-outage",
        "cache_hit": 0.05,       # cache misses fall through to the DB...
        "db_pool_active": 42,    # ...spiking DB load...
        "latency_p95_ms": 1400,  # ...and overall latency
        "error_rate": 0.05,
        "log": "CacheConnectionError: connection refused to cache cluster; falling back to database",
    },
}


def profile_for(fault_id: str | None) -> dict:
    """Merge a fault's overrides onto BASELINE. None -> healthy baseline."""
    merged = dict(BASELINE)
    if fault_id and fault_id in FAULTS:
        merged.update({k: v for k, v in FAULTS[fault_id].items()})
    return merged
