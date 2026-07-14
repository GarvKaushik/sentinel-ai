# Runbook: API Latency Spike

## Symptoms
- p95/p99 latency on one or more API endpoints increases significantly
  above baseline (typically >2x normal p95 within a 5-minute window)
- Often accompanied by a rise in request queue depth or thread pool
  saturation metrics
- May or may not correlate with an increase in error rate — latency
  spikes without errors usually point to a downstream dependency or
  resource contention issue, not a code defect

## Common Root Causes (in order of frequency)

1. **Downstream dependency slowdown.** A service this API calls
   (database, third-party API, internal microservice) has itself
   slowed down, and calls are blocking waiting for a response.
   Check the dependency's own latency metrics first — if it's also
   elevated, the root cause is upstream, not in this service.

2. **Database connection pool exhaustion.** If the connection pool is
   maxed out, new requests queue waiting for a free connection, adding
   latency that looks like it's coming from "the API" but is actually
   a pool-sizing or slow-query problem. Check
   `db_pool_active_connections` vs `db_pool_max_connections` — if
   they're equal or close, this is likely it.

3. **A recent deploy introduced a slow code path.** Check deploy
   history for anything that landed in the ~15 minutes before the
   latency spike began. Look specifically for changes to the request
   handling path for the affected endpoint — a deploy to an unrelated
   service deployed around the same time is not automatically the
   cause; correlate by which service actually owns the slow endpoint.

4. **Resource exhaustion (CPU/memory) on the service itself.** Check
   CPU and memory utilization on the affected pods/instances. Memory
   pressure in particular can cause GC pauses that manifest as latency
   spikes rather than crashes.

5. **Traffic spike within normal capacity limits.** Sometimes latency
   rises simply because request volume increased and the service is
   operating near its designed capacity. This is NOT a bug — check
   request-per-second metrics against historical baselines before
   assuming something is broken. If RPS is significantly above normal
   and error rate stays low, this may just require scaling up, not a
   code fix.

## Diagnostic Steps

1. Pull p50/p95/p99 latency for the affected endpoint over the last 30
   minutes, compare against the same window 24 hours prior.
2. Check error rate for the same window — elevated errors alongside
   latency points toward a different failure mode (see the Error Rate
   Spike runbook) rather than pure latency degradation.
3. Check downstream dependency latency/error metrics.
4. Check DB connection pool utilization.
5. Check deploy history for the affected service specifically (not
   just "any deploy in the timeframe" — correlate by service ownership).
6. Check CPU/memory on affected instances.
7. Check request volume (RPS) against historical baseline.

## Remediation

- **Downstream slowdown:** escalate to the owning team of the slow
  dependency; consider adding/tightening timeouts and circuit breakers
  on the calling side as a stopgap.
- **Connection pool exhaustion:** increase pool size if headroom exists
  on the DB side, or identify and fix the slow query holding
  connections longer than expected.
- **Bad deploy:** roll back the specific commit identified via deploy
  correlation, not just "the most recent deploy" — verify the rollback
  target by checking which commit actually touches the affected code path.
- **Resource exhaustion:** scale up instance count or size; investigate
  memory leak if this recurs without a corresponding traffic increase.
- **Traffic within capacity:** scale horizontally; this is expected
  behavior at higher load, not a defect.
