# Runbook: Disk Space Exhaustion

## Symptoms
- `disk_used_pct` climbs toward 100% on the affected instance/volume
- Write operations begin failing: `No space left on device`,
  `IOError`, failed log writes, failed uploads or temp-file creation
- Errors are concentrated on write paths; read-only endpoints may keep
  working, which can make the failure look partial
- Databases or brokers on the same volume may stall or refuse writes

## Common Root Causes

1. **Unbounded log growth** — verbose or debug logging with no rotation
   filling the volume.

2. **Accumulating temp files, caches, or artifacts** that are written
   but never cleaned up.

3. **A large data import, backup, or migration** consuming the volume in
   a burst.

4. **An undersized volume** for the workload's normal write throughput.

## Diagnostic Steps

1. Plot `disk_used_pct` over the window — a steady climb to ~100% is the
   primary signal.
2. Identify what is consuming space (logs, temp dirs, data files, core
   dumps) on the affected volume.
3. Check whether a recent change raised log verbosity or added a new
   write path without cleanup.
4. Check for a backup/import/migration job running in the window.
5. Confirm which services share the volume and are affected by the
   write failures.

## Remediation

- **Log growth:** enable/repair log rotation and retention; reduce
  verbosity; ship logs off-box instead of storing locally.
- **Temp/artifact buildup:** add cleanup (TTL/cron) for temp and cache
  directories.
- **Immediate mitigation:** free space (rotate/compress/delete safe
  files) to recover writes, then address the root cause.
- **Undersized volume:** expand the volume if the write demand is
  legitimate.
- **Follow-up:** add a disk-utilization alert well below 100% so the
  climb is caught before writes fail.
