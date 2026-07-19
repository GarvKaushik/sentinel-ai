# Runbook: Configuration Drift

## Symptoms
- Errors begin **without any corresponding deploy** — the code did not
  change, the environment did
- Error messages point at configuration: `ConfigurationError`,
  `unset environment variable`, `invalid value for`, missing feature-flag
  or secret
- Failures are often all-or-nothing for one code path (the one guarded
  by the drifted setting) rather than a broad error-rate rise
- Frequently follows a config push, a feature-flag toggle, a secret
  rotation, or a manual change outside the deploy pipeline

## Common Root Causes

1. **A required config value was changed, unset, or rotated** out of
   band (feature-flag flip, changed environment variable, expired or
   rotated secret) so a code path that depends on it now fails closed.

2. **Environment mismatch** — a value correct in staging is wrong or
   absent in production, so the path works in tests and fails in prod.

3. **A feature flag enabled a code path whose dependent configuration
   was never provisioned** in the target environment.

4. **Expired credential or certificate** that silently stops working at
   its expiry time, unrelated to any deploy.

## Diagnostic Steps

1. Confirm there was **no deploy** in the incident window — if the code
   is unchanged, suspect configuration first.
2. Read the error message for the specific config key, flag, or secret
   it names.
3. Check the configuration/feature-flag change history for a change
   near the incident onset.
4. Compare the effective config in the failing environment against a
   known-good environment for that key.
5. Check for recently rotated or expired secrets/certificates.

## Remediation

- **Reverted/incorrect value:** restore the correct configuration value
  or flag state; confirm the failing code path recovers.
- **Missing provisioning:** provision the required config/secret in the
  affected environment before re-enabling the dependent feature flag.
- **Expired credential:** rotate/renew and redeploy the secret; add an
  expiry alert so it is renewed ahead of time next cycle.
- **Follow-up:** bring the drifted setting under version-controlled,
  reviewed config management so out-of-band changes cannot recur.
