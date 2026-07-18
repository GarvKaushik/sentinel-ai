# Runbook: CPU Saturation

## Symptoms
- `cpu_usage_pct` sits at or near 100% on the affected instances
- Latency (p95/p99) rises broadly across all endpoints, not just one —
  everything slows because requests wait for CPU time
- Throughput plateaus even as request volume rises
- Usually **no** spike in error rate at first — requests are slow, not
  failing, until timeouts downstream begin to trip

## Common Root Causes

1. **A new, CPU-expensive code path** shipped in a recent deploy (an
   inefficient algorithm, regex catastrophe, unbounded loop, or
   accidental synchronous work on a hot path).

2. **Under-provisioning for current load** — the service is simply at
   its CPU capacity for the traffic it receives.

3. **A noisy neighbor or throttling** — CPU limits/quotas throttling the
   container even though the node has headroom.

4. **Excessive serialization/deserialization or compression** on large
   payloads introduced or amplified recently.

## Diagnostic Steps

1. Confirm `cpu_usage_pct` is saturated for the affected instances over
   the window, and that latency rose in step with it.
2. Check whether error rate stayed low (pure saturation) or whether
   downstream timeouts began (secondary effect).
3. Check deploy history for a change to a hot code path just before the
   climb.
4. Compare request volume against baseline — saturation from a traffic
   rise vs. from a code regression are handled differently.
5. If available, capture a CPU profile/flame graph to find the hot
   function.

## Remediation

- **Code regression:** roll back or optimize the expensive path
   identified by the profile; add a regression benchmark.
- **Under-provisioned:** scale out (more replicas) or up (more CPU) if
  the load is legitimate.
- **Throttling:** raise the CPU limit/quota if the node has headroom.
- **Follow-up:** add a CPU-saturation alert and a latency SLO so this is
  caught before it cascades into downstream timeouts.
