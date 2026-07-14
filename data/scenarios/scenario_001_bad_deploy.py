"""
Scenario 001: Bad Deploy → Null Reference Exception

Hand-crafted (not LLM-generated) on purpose — this is the reference
scenario you build the whole pipeline against before automating the
other 5-7 templates. If your agents can't correctly solve THIS one,
don't bother scaling up scenario generation yet.

Story: A new commit to svc-payments removes a null-check on a field that
a downstream client still sometimes omits. Error rate spikes ~90 seconds
after the deploy lands. There's also a decoy: a totally unrelated commit
to svc-notifications landed 2 minutes earlier and touches nothing relevant
— this tests whether your Root-Cause agent naively blames "whatever
deployed most recently" vs. actually correlating the affected service.
"""

from app.schemas.scenario import IncidentScenario, MetricPoint, LogEntry, CommitInfo

scenario_001 = IncidentScenario(
    scenario_id="scenario_001_bad_deploy",
    title="Payments API error rate spike after deploy",
    services_affected=["svc-payments"],
    injected_root_cause=(
        "Commit a1b2c3d removed a null-check on `customer.billing_address` "
        "in the payments validation path, causing NullPointerException for "
        "any request where billing_address is omitted (a valid, common case "
        "for digital-goods-only orders)."
    ),
    root_cause_category="bad_deploy",

    metrics=[
        MetricPoint(timestamp="2026-07-06T14:30:00Z", metric_name="error_rate_pct", value=0.3, service="svc-payments"),
        MetricPoint(timestamp="2026-07-06T14:31:00Z", metric_name="error_rate_pct", value=0.4, service="svc-payments"),
        MetricPoint(timestamp="2026-07-06T14:32:00Z", metric_name="error_rate_pct", value=0.5, service="svc-payments"),
        # deploy lands at 14:32:30
        MetricPoint(timestamp="2026-07-06T14:33:00Z", metric_name="error_rate_pct", value=12.8, service="svc-payments"),
        MetricPoint(timestamp="2026-07-06T14:34:00Z", metric_name="error_rate_pct", value=38.4, service="svc-payments"),
        MetricPoint(timestamp="2026-07-06T14:35:00Z", metric_name="error_rate_pct", value=41.2, service="svc-payments"),
        # unrelated service stays flat the whole time — important negative signal
        MetricPoint(timestamp="2026-07-06T14:30:00Z", metric_name="error_rate_pct", value=0.2, service="svc-notifications"),
        MetricPoint(timestamp="2026-07-06T14:35:00Z", metric_name="error_rate_pct", value=0.2, service="svc-notifications"),
    ],

    logs=[
        LogEntry(
            timestamp="2026-07-06T14:33:04Z",
            service="svc-payments",
            level="ERROR",
            message=(
                "NullPointerException at PaymentValidator.validate(PaymentValidator.java:142): "
                "Cannot invoke \"BillingAddress.getPostalCode()\" because "
                "\"customer.billing_address\" is null"
            ),
            line_id="log:svc-payments:line:4821",
        ),
        LogEntry(
            timestamp="2026-07-06T14:33:47Z",
            service="svc-payments",
            level="ERROR",
            message=(
                "NullPointerException at PaymentValidator.validate(PaymentValidator.java:142): "
                "Cannot invoke \"BillingAddress.getPostalCode()\" because "
                "\"customer.billing_address\" is null"
            ),
            line_id="log:svc-payments:line:4855",
        ),
        LogEntry(
            timestamp="2026-07-06T14:30:12Z",
            service="svc-notifications",
            level="INFO",
            message="Scheduled digest job completed successfully, 4213 emails queued",
            line_id="log:svc-notifications:line:991",
        ),
    ],

    deploy_history=[
        CommitInfo(
            sha="a1b2c3d",
            author="garv",
            timestamp="2026-07-06T14:32:30Z",
            message="Refactor PaymentValidator: simplify address validation logic",
            files_changed=["src/main/java/payments/PaymentValidator.java"],
            is_guilty_commit=True,
        ),
        CommitInfo(
            sha="e4f5g6h",
            author="teammate",
            timestamp="2026-07-06T14:30:15Z",
            message="Update email digest template copy",
            files_changed=["src/main/java/notifications/DigestTemplate.java"],
            is_guilty_commit=False,  # the decoy — deployed even closer to alert time
        ),
    ],

    red_herrings=[
        "commit e4f5g6h deployed only ~2 minutes before the alert, "
        "and to the naive eye 'most recent deploy' looks suspicious — "
        "but it touches svc-notifications, which shows zero error-rate "
        "movement in the metrics. A correct investigation should rule "
        "this out on the basis of service-correlation, not just recency."
    ],

    expected_evidence_refs=[
        "prometheus:error_rate_pct:svc-payments:2026-07-06T14:33:00Z",
        "log:svc-payments:line:4821",
        "commit:a1b2c3d",
    ],
)
