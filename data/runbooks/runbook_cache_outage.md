# Runbook: Cache Outage / Stampede

## Symptoms
- `cache_hit_ratio` drops sharply (often toward zero) at the onset
- Latency rises broadly and the **backing database load spikes** at the
  same moment — traffic that the cache used to absorb now hits the DB
- May escalate into DB connection-pool exhaustion or timeouts as a
  secondary effect
- Follows a cache node/cluster restart, eviction, a flushed cache, or a
  mass key expiry

## Common Root Causes

1. **The cache became unavailable** (Redis/Memcached node down, network
   partition, restart), so every request misses and falls through to the
   database.

2. **A cache stampede / thundering herd** — many keys expired at once (or
   the cache was flushed), so concurrent requests all recompute and
   hammer the DB simultaneously.

3. **A cache flush or deploy that cleared/invalidated the cache**,
   leaving it cold under live traffic.

4. **Cache capacity too small**, causing aggressive eviction and a
   chronically low hit ratio.

## Diagnostic Steps

1. Plot `cache_hit_ratio` — a sharp drop at onset is the primary signal.
2. Check the cache backend's own health (node up? memory? evictions?).
3. Correlate the DB load/latency spike with the cache-miss rise to
   confirm fall-through is the mechanism.
4. Check for a cache restart, flush, deploy, or synchronized key expiry
   at the onset time.
5. Confirm whether the DB then hit secondary limits (pool exhaustion,
   timeouts) as a downstream effect.

## Remediation

- **Cache down:** restore the cache node/cluster; the hit ratio and DB
  load should recover together.
- **Stampede:** add request coalescing / single-flight and stagger TTLs
  (add jitter) so keys do not all expire at once; warm the cache before
  serving traffic after a flush.
- **Cold cache after deploy:** pre-warm critical keys before shifting
  traffic.
- **Undersized cache:** increase capacity to restore the hit ratio.
- **Follow-up:** alert on `cache_hit_ratio` drops and add a DB-load
  guard so a cache outage degrades gracefully instead of overloading the
  database.
