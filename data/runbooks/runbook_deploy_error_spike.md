# Runbook: Deploy-Induced Error Rate Spike

## Symptoms
- Error rate (5xx responses, exceptions, or failed transactions) rises
  sharply, often within seconds to a few minutes of a deploy landing
- Errors are frequently concentrated on a specific endpoint or code
  path rather than uniform across the whole service
- Stack traces in logs typically point to a specific file/line that
  was touched by a recent commit

## Common Root Causes

1. **Null/undefined reference on a field that's usually but not
   always present.** A very common pattern: a refactor removes or
   loosens a null-check on a field that's optional in some valid
   request shapes (e.g. digital-only orders lacking a shipping/billing
   address). The bug doesn't show up in testing if test fixtures
   always populate the field, but does show up in production traffic
   where the field is legitimately sometimes absent.

2. **Schema/contract mismatch after a deploy.** One service starts
   sending or expecting a field shape the other side wasn't updated
   to handle — common when a deploy to a producer service isn't
   coordinated with consumers.

3. **Missing or misconfigured feature flag / config value** that the
   new code path depends on, causing it to fail closed in production
   despite working in staging (where the config often differs).

4. **Dependency version bump with a breaking change** in a library
   update bundled into the same deploy.

## Diagnostic Steps — Correlating the Right Commit

**This is the step most likely to go wrong under time pressure: do
not assume the most recently deployed commit is the guilty one just
because of recency.** Multiple services or components may deploy
around the same time. Correlate by:

1. Which **service** is actually showing the elevated error rate.
2. Whether the suspect commit's **files_changed** actually touch the
   code path referenced in the error stack trace.
3. Whether the **timing** lines up precisely — the error spike should
   begin at or shortly after the commit's actual deploy timestamp, not
   just "sometime in the same rough window."

A deploy to an unrelated service that happens to land 1-2 minutes
before an unrelated service's incident is a common decoy — always
verify service ownership and code-path relevance, not just proximity
in time, before naming a commit as the root cause.

## Remediation

- **Immediate:** roll back the specific commit confirmed via the
  correlation steps above. Do not roll back unrelated deploys "just
  in case" — this delays resolution and adds noise to the incident
  timeline.
- **Fix-forward alternative:** if rollback isn't feasible (e.g. it
  would also revert unrelated needed changes), patch the specific
  null-check or config issue and deploy a hotfix.
- **Follow-up:** add a test case covering the specific missing-field
  scenario that caused the incident, so the regression is caught
  before the next deploy.
