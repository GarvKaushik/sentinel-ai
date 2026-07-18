# Runbook: Upstream Dependency Timeout

## Symptoms
- Error rate rises with a large share of `504 Gateway Timeout`,
  `UpstreamTimeout`, or `DeadlineExceeded` errors rather than `500`s
- The affected service's own CPU/memory look healthy — it is waiting,
  not working
- Latency climbs to right around the configured client timeout value
  (e.g. a wall of requests all taking ~2000ms) before failing
- A specific downstream call in the trace is the one that stalls

## Common Root Causes

1. **A downstream service or third-party API became slow or
   unavailable.** The caller blocks until its timeout fires, then
   returns an error. The root cause is in the dependency, not in the
   service reporting the errors.

2. **A timeout set too tight for a legitimately slow operation**, so a
   dependency that is merely slow (not broken) trips the deadline.

3. **Missing circuit breaker**, so every request keeps hammering an
   already-failing dependency instead of failing fast.

4. **Connection pool to the dependency exhausted**, so requests queue
   waiting for a client connection and time out before they are served.

## Diagnostic Steps

1. Identify which downstream call is timing out from the error message
   or trace span.
2. Check that dependency's own latency and error metrics — if they are
   elevated, the root cause is upstream of this service.
3. Confirm whether the failures cluster exactly at the configured
   timeout value (a strong signal it is a timeout, not a crash).
4. Check whether a recent deploy changed the timeout, retry, or client
   configuration for that dependency.
5. Rule out network/DNS issues between this service and the dependency.

## Remediation

- **Dependency is down/slow:** escalate to the owning team; enable or
  tighten a circuit breaker so this service fails fast instead of
  exhausting its own resources waiting.
- **Timeout too tight:** raise the timeout only if the dependency's
  normal latency genuinely needs it; do not paper over a real slowdown.
- **No circuit breaker:** add one plus a sensible fallback (cached
  response, graceful degradation) so a single dependency cannot take
  the whole service down.
- **Follow-up:** add an alert on downstream call latency so the
  dependency degradation is caught before it becomes user-facing errors.
