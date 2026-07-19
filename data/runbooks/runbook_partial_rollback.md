# Runbook: Partial Rollback / Version Skew

## Symptoms
- Errors are **intermittent** — a fraction of requests fail while others
  succeed, because traffic is split across mixed pod versions
- Error messages point at a contract/schema mismatch:
  `ContractMismatchException`, `unknown field`, `cannot deserialize`,
  version-incompatible response
- The error rate does not return fully to baseline after a rollback —
  it partially improves, which is the tell
- Follows a deploy that was rolled back, paused mid-rollout, or a failed
  canary that left old and new pods coexisting

## Common Root Causes

1. **A rollback or paused rollout left old and new versions running
   simultaneously**, and the two speak incompatible API/schema versions,
   so cross-version calls fail while same-version calls succeed.

2. **A producer/consumer contract change deployed to only one side** —
   e.g. the producer emits v2 while some consumers still expect v1.

3. **A database migration applied against mixed application versions**,
   where old code cannot read the new schema (or vice versa).

## Diagnostic Steps

1. Confirm the failures are **intermittent**, not uniform — the hallmark
   of version skew across a fleet.
2. Check the rollout/rollback history for an incomplete or reverted
   deployment leaving mixed versions live.
3. Inspect the error message for the specific field/schema version that
   mismatches.
4. Correlate which pods (old vs new image tag) serve failing requests.
5. Check whether a schema/contract change shipped to only one side of a
   producer/consumer pair.

## Remediation

- **Complete the rollout in one direction:** either finish rolling
  forward to a single consistent version, or fully roll back all pods —
  do not leave the fleet split.
- **Contract mismatch:** deploy the compatible version to the lagging
  side, or make the change backward-compatible before splitting traffic.
- **Migration skew:** use expand/contract migrations so both versions
  can read the schema during the transition.
- **Follow-up:** enforce backward-compatible contract changes and
  version-aware routing so a partial rollout degrades gracefully.
