# Runbook: Traffic Spike Within Capacity (No Bug)

## Symptoms
- `requests_per_second` rises sharply and well above the historical
  baseline
- Latency rises with the load, but **error rate stays low** — the
  service is serving successfully, just more slowly
- No recent deploy correlates with the onset; the change is in demand,
  not in the code
- The rise often lines up with an external event (marketing push,
  sale, cron burst, retry storm from a client)

## Common Root Causes

1. **Legitimate demand increase** — a real surge in traffic the service
   can still serve, operating nearer its capacity ceiling. This is
   **not a defect**; forcing a code root cause here is a mistake.

2. **A client retry storm** amplifying real traffic — worth
   distinguishing from organic demand, but still not a bug in this
   service.

3. **A scheduled job or batch fan-out** hitting the service in a burst.

## Diagnostic Steps

1. Plot `requests_per_second` against the historical baseline — a large,
   sustained rise is the primary signal.
2. Confirm **error rate stayed low** — elevated errors would point to a
   different failure mode, not a clean traffic spike.
3. Confirm there was **no correlated deploy** — this rules out a code
   regression masquerading as load.
4. Check whether the surge is organic (broad client base) or a retry
   storm / single-source burst.
5. Compare current load against the service's known capacity limits.

## Remediation

- **Organic demand within capacity:** scale horizontally to restore
  latency headroom; this is expected behavior at higher load, not a
  fix-the-code situation.
- **Retry storm:** work with the offending client to add backoff/jitter
  and cap retries.
- **Batch burst:** reschedule or rate-limit the job so it does not
  overlap peak traffic.
- **Important:** do not roll back a deploy or "fix" code for a clean
  traffic spike — the correct response is capacity, not a code change.
