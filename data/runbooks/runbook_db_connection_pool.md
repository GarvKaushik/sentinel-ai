# Runbook: Database Connection Pool Exhaustion

## Symptoms
- Requests begin timing out or queuing, often presenting as a latency
  spike rather than an obvious "database is down" error
- `db_pool_active_connections` approaches or equals
  `db_pool_max_connections`
- Frequently follows a slow query being introduced or an increase in
  concurrent request volume that the pool wasn't sized for

## Common Root Causes

1. **A newly introduced slow query** holds connections open longer
   than usual, reducing effective pool availability for other requests
   even though the pool size itself hasn't changed.

2. **Connection leak** — a code path acquires a connection but fails
   to release it back to the pool under certain error conditions
   (missing `finally`/context-manager cleanup on an exception path).

3. **Traffic increase without a corresponding pool size increase** —
   the pool was sized for a previous, lower traffic baseline.

4. **A long-running background job or migration** holding connections
   during a window that overlaps with normal request traffic.

## Diagnostic Steps

1. Check `db_pool_active_connections` vs `db_pool_max_connections`
   for the affected service over the incident window.
2. Check query duration percentiles on the database itself — a new
   slow query will show up as a shift in the p95/p99 query duration
   distribution, not necessarily in overall query count.
3. Check deploy history for anything touching query logic or
   connection handling code (e.g. missing `finally` blocks, changed
   ORM query patterns) in the window before the spike.
4. Check for scheduled jobs or migrations running in the same window.
5. Compare current request volume against historical baseline to
   rule out (or confirm) simple traffic growth as the cause.

## Remediation

- **Slow query introduced:** identify and optimize/index the query,
  or roll back the commit that introduced it.
- **Connection leak:** patch the missing cleanup path; add a pool
  utilization alert with a lower threshold to catch this earlier next
  time.
- **Pool undersized for traffic:** increase max pool size (verify the
  database itself has headroom for more concurrent connections first).
- **Competing background job:** reschedule the job outside of peak
  traffic windows, or give it a separate, smaller connection pool.
