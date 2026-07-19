"""Sentinel AI — demo cockpit (Streamlit).

One screen to drive the demo: see service status, inject any of the 12 faults
(and heal), watch the live telemetry, run an investigation, and browse history.

Run (with the stack up):  streamlit run ui/app.py
"""

from __future__ import annotations

from pathlib import Path

import requests
import streamlit as st

import services as svc

LOGO = str(Path(__file__).parent / "sentinel_logo.png") 

st.set_page_config(page_title="Sentinel AI — Demo Cockpit", page_icon=LOGO, layout="wide")
st.logo(LOGO, size="large")

# Hide Streamlit's hover "fullscreen/zoom" button on images and charts.
st.markdown(
    '<style>button[title="View fullscreen"],'
    '[data-testid="StyledFullScreenButton"]{display:none!important;}</style>',
    unsafe_allow_html=True,
)

_, _mid, _ = st.columns([1, 1, 1])
_mid.image(LOGO, use_column_width=True)
st.caption("Break the dummy service, then let Sentinel investigate the live incident — every claim cited to real evidence.")


def _fmt(v, suffix=""):
    return "—" if v is None else f"{v:,.2f}{suffix}"


def render_result(hypotheses, recommendation, postmortem_md):
    """Render a finished investigation (shared by the async + sync code paths)."""
    st.markdown("#### Ranked hypotheses")
    for h in hypotheses or []:
        badge = "✅" if h["status"] == "survived_critique" else "⬇️"
        st.markdown(f"{badge} **{h['status']}** · confidence `{h['confidence']:.2f}`  \n{h['description']}")

    if recommendation:
        label = "Recommendation" + (" — escalated to human" if recommendation.get("is_fallback_escalation") else "")
        st.markdown(f"#### {label}")
        st.write(recommendation.get("summary", ""))
        for step in recommendation.get("detailed_steps") or []:
            st.markdown(f"- {step}")

    if postmortem_md:
        with st.expander("Full cited postmortem"):
            st.markdown(postmortem_md)


# ---------------------------------------------------------------- services
st.subheader("Services")
status_cols = st.columns(3)
for (name, (up, link)), col in zip(svc.service_status().items(), status_cols):
    col.metric(name, "🟢 UP" if up else "🔴 DOWN")

st.divider()

# ---------------------------------------------------------------- faults
st.subheader("Fault injection")
try:
    faults = svc.get_faults()
    active = faults["available"]
    if faults["active"]:
        st.warning(f"⚠️ Active fault: **{faults['active']}** — telemetry is anomalous")
    else:
        st.success("✅ No active fault — service healthy")

    cols = st.columns(4)
    for i, (fid, category) in enumerate(sorted(active.items())):
        if cols[i % 4].button(fid, help=f"category: {category}", use_container_width=True):
            svc.inject_fault(fid)
            st.rerun()

    if st.button("🩹 Clear fault / heal service", type="primary"):
        svc.clear_fault()
        st.rerun()
except requests.RequestException:
    st.error("Dummy target not reachable. Is the stack up? `docker compose up -d dummy prometheus`")

st.divider()

# ---------------------------------------------------------------- telemetry
st.subheader("Live telemetry")
try:
    t = st.columns(5)
    t[0].metric("Error rate", _fmt(svc.prom_instant(svc.Q_ERROR_RATE), "%"))
    t[1].metric("p95 latency", _fmt(svc.prom_instant(svc.Q_LATENCY_P95), " ms"))
    t[2].metric("Req/s", _fmt(svc.prom_instant(svc.Q_RPS)))
    t[3].metric("CPU", _fmt(svc.prom_instant(svc.Q_CPU)))
    t[4].metric("Cache hit", _fmt(svc.prom_instant(svc.Q_CACHE)))

    series = svc.prom_range(svc.Q_ERROR_RATE, minutes=10)
    if series:
        st.line_chart({"error rate %": series}, height=200)

    c1, c2 = st.columns(2)
    with c1.expander("Recent logs"):
        logs = svc.get_logs(limit=15)
        if logs:
            for e in reversed(logs):
                st.text(f"[{e['level']}] {e['message'][:110]}")
        else:
            st.caption("no logs yet")
    with c2.expander("Deploy history"):
        deploys = svc.get_deploys()
        if deploys:
            for d in deploys:
                flag = " (guilty)" if d.get("is_guilty_commit") else ""
                st.text(f"{d['sha']}  {d['message']}{flag}")
        else:
            st.caption("no deploys recorded")
except requests.RequestException:
    st.info("Telemetry unavailable — Prometheus/dummy not reachable.")

st.divider()

# ---------------------------------------------------------------- investigate
st.subheader("Run investigation")
st.caption("Sends an alert to Sentinel, which builds an incident from live telemetry and runs the full pipeline.")

i1, i2, i3 = st.columns([2, 2, 1])
service = i1.text_input("Service", svc.SERVICE)
metric = i2.selectbox(
    "Alert metric",
    ["error_rate_pct", "latency_p95_ms", "cpu_usage_ratio", "db_pool_active_connections", "cache_hit_ratio", "requests_per_second"],
)
window = i3.slider("Window (min)", 3, 15, 8)

if st.button("🔍 Investigate live incident", type="primary"):
    try:
        with st.spinner("Enqueuing the alert…"):
            submit = svc.run_alert(service, metric, window)
    except requests.RequestException as e:
        st.error(f"Alert failed — is the Sentinel API running? ({e})")
        st.stop()

    # Async path: /alert returned a job handle → a Celery worker runs it and we
    # poll Postgres for the result.
    if submit.get("status") == "queued" and submit.get("investigation_id") is not None:
        inv_id = submit["investigation_id"]
        job = str(submit.get("job_id") or "")[:8]
        st.info(f"Queued as investigation **#{inv_id}** (job `{job}…`) — a Celery worker is running it off the request path.")
        with st.spinner("Worker investigating — building the incident from live telemetry and running the agents (~20s)…"):
            try:
                record = svc.poll_investigation(inv_id)
            except requests.RequestException as e:
                st.error(f"Lost contact while polling: {e}")
                st.stop()

        if record.get("status") == "failed":
            st.error(f"Investigation failed: {record.get('error')}")
            st.stop()
        if record.get("status") != "done":
            st.warning(f"Still `{record.get('status')}` after the wait — is the worker up? (`docker compose ps worker`). Re-check it in history below.")
            st.stop()

        ledger = record.get("ledger") or {}
        hypotheses = ledger.get("hypotheses", [])
        recommendation = ledger.get("recommendation")
        postmortem_md = record.get("postmortem_markdown")
        st.success(f"Investigated **{record.get('service', service)}** on `{record.get('metric', metric)}` — {record.get('evidence_count', '?')} evidence items (investigation #{inv_id})")
    else:
        # Synchronous fallback shape (no queue configured).
        src = submit.get("source", {})
        hypotheses = submit.get("hypotheses", [])
        recommendation = submit.get("recommendation")
        postmortem_md = submit.get("postmortem_markdown")
        st.success(f"Investigated **{src.get('service', service)}** on `{src.get('metric', metric)}` — {src.get('metric_points', '?')} metric points, {submit.get('evidence_count', '?')} evidence items")

    render_result(hypotheses, recommendation, postmortem_md)

st.divider()

# ---------------------------------------------------------------- history
st.subheader("Investigation history")
st.caption("Every run is persisted to Postgres — most recent first. **Click a row** to open the full cited investigation.")
try:
    rows = svc.list_investigations(limit=15)
    if rows:
        table = [
            {
                "id": r["id"],
                "when": (r.get("created_at") or "")[:19].replace("T", " "),
                "trigger": r.get("trigger"),
                "service": r.get("service"),
                "status": r.get("status"),
                "root cause": (r.get("top_root_cause") or "")[:70],
                "conf": r.get("confidence"),
            }
            for r in rows
        ]
        event = st.dataframe(
            table,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        selected = getattr(getattr(event, "selection", None), "rows", []) or []
        if selected:
            chosen = rows[selected[0]]
            inv_id = chosen["id"]
            try:
                detail = svc.get_investigation(inv_id)
            except requests.RequestException as e:
                st.error(f"Could not load investigation #{inv_id}: {e}")
                detail = None

            if detail:
                st.markdown(
                    f"### Investigation #{inv_id} — "
                    f"{detail.get('service') or '—'} on `{detail.get('metric') or '—'}` "
                    f"· status `{detail.get('status')}`"
                )
                if detail.get("status") == "failed":
                    st.error(detail.get("error") or "Investigation failed.")
                elif detail.get("status") != "done":
                    st.info(f"This investigation is still `{detail.get('status')}` — check back shortly.")
                else:
                    ledger = detail.get("ledger") or {}
                    render_result(
                        ledger.get("hypotheses", []),
                        ledger.get("recommendation"),
                        detail.get("postmortem_markdown"),
                    )
    else:
        st.caption("No investigations recorded yet — run one above.")
except requests.RequestException:
    st.info("History unavailable — Sentinel API not reachable.")
