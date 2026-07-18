# Runbook: Memory Leak / OOM Kills

## Symptoms
- `memory_working_set_bytes` (or RSS) climbs steadily over minutes/hours
  and does not fall after traffic subsides
- Pods/containers are `OOMKilled` and restart, producing periodic error
  bursts around each restart
- Latency degrades before each kill as garbage collection thrashes
  under memory pressure
- The sawtooth pattern (climb → kill → reset → climb again) is
  characteristic

## Common Root Causes

1. **A genuine memory leak** — objects retained that should be freed
   (unbounded cache, growing collection, unclosed resources), so memory
   grows monotonically until the limit is hit.

2. **An unbounded in-memory buffer or cache** with no eviction policy
   that grows with unique inputs.

3. **A memory-limit set too low** for the workload's legitimate working
   set, so normal operation trips the limit.

4. **A large recent change in payload size or concurrency** that raised
   the working set above the configured limit.

## Diagnostic Steps

1. Plot `memory_working_set_bytes` over the incident window — a steady
   climb that resets on restart points to a leak or unbounded buffer.
2. Correlate restarts/OOM-kill events with the memory ceiling.
3. Check deploy history for a change to caching, buffering, or object
   lifecycle in the window before the climb began.
4. Check whether request volume or payload size grew (working set up for
   a legitimate reason vs. a leak).
5. If available, capture a heap profile to find the retained objects.

## Remediation

- **Leak / unbounded cache:** fix the retention bug or add a bounded
  eviction policy (max size, TTL); roll back the introducing commit if
  one is identified.
- **Limit too low:** raise the memory limit only after confirming the
  working set is legitimate, not leaking.
- **Immediate mitigation:** a rolling restart clears the leaked memory
  temporarily to buy time, but is not a fix.
- **Follow-up:** add a memory-utilization alert below the OOM threshold
  so the climb is caught before kills start.
