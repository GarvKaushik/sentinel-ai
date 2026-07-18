# Runbook: Upstream Rate-Limit Exhaustion (429s)

## Symptoms
- A rise in `429 Too Many Requests` from an upstream/third-party API,
  surfacing as elevated error rate on the calling service
- Errors correlate with a **quota window** — they start when a per-minute
  or per-day limit is hit and may clear when the window resets
- Latency may rise if the client retries the 429s without backoff
- Often follows a traffic increase, a new high-volume caller, or a
  reduced quota on the provider side

## Common Root Causes

1. **The service exceeded the upstream API's rate/quota limit** — its
   own request volume to the dependency crossed the allowed threshold.

2. **A retry storm amplifying the problem** — retrying 429s without
   backoff multiplies the request volume and keeps the quota pinned.

3. **A quota reduction or plan change on the provider side** that
   lowered the ceiling without a corresponding change in usage.

4. **Missing client-side rate limiting**, so bursts are sent straight
   through instead of being smoothed under the quota.

## Diagnostic Steps

1. Confirm the errors are `429`s from a specific upstream, not `500`s
   originating locally.
2. Check the calling service's outbound request rate to that upstream
   against the known quota.
3. Check whether retries lack backoff (which converts a brief limit into
   a sustained storm).
4. Check for a recent traffic increase or a new caller sharing the same
   quota.
5. Confirm with the provider whether the quota changed recently.

## Remediation

- **Over quota:** add client-side rate limiting / token-bucket throttling
  to stay under the ceiling; request a quota increase if usage is
  legitimate.
- **Retry storm:** add exponential backoff with jitter and honor the
  `Retry-After` header; cap retry attempts.
- **Quota reduced:** negotiate the quota or shed non-critical traffic to
  the dependency.
- **Follow-up:** add an alert on outbound request rate approaching the
  quota so it is caught before 429s reach users.
