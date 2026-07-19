"""
Sentinel AI — Demo Cockpit (Streamlit).

One screen to drive the whole demo:
  * see every service's status,
  * inject any of the 12 faults with one click (and heal),
  * watch the live telemetry move,
  * run a full investigation and read the cited postmortem.

Run (with the stack already up via `docker compose up`):
    streamlit run ui/app.py
"""

from __future__ import annotations

from pathlib import Path

import requests
import streamlit as st

import services as svc

LOGO = str(Path(__file__).parent / "sentinel_logo.png")  # transparent-background version

st.set_page_config(page_title="Sentinel AI — Demo Cockpit", page_icon=LOGO, layout="wide")
st.logo(LOGO, size="large")

_, _mid, _ = st.columns([1, 1, 1])
_mid.image(LOGO, use_column_width=True)
st.caption("Break the dummy service, then let Sentinel investigate the live incident — every claim cited to real evidence.")


def _fmt(v, suffix=""):
    return "—" if v is None else f"{v:,.2f}{suffix}"


# ---------------------------------------------------------------- services
st.subheader("Services")
status_cols = st.columns(3)
for (name, (up, link)), col in zip(svc.service_status().items(), status_cols):
    col.metric(name, "🟢 UP" if up else "🔴 DOWN")
    col.caption(f"[open]({link})")

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
    with st.spinner("Investigating — building the incident from live telemetry and running the agents (~20s)…"):
        try:
            res = svc.run_alert(service, metric, window)
        except requests.RequestException as e:
            st.error(f"Investigation failed — is the Sentinel API running? ({e})")
            st.stop()

    src = res.get("source", {})
    st.success(f"Investigated **{src.get('service', service)}** on `{src.get('metric', metric)}` — {src.get('metric_points', '?')} metric points, {res['evidence_count']} evidence items")

    st.markdown("#### Ranked hypotheses")
    for h in res["hypotheses"]:
        badge = "✅" if h["status"] == "survived_critique" else "⬇️"
        st.markdown(f"{badge} **{h['status']}** · confidence `{h['confidence']:.2f}`  \n{h['description']}")

    rec = res["recommendation"]
    label = "Recommendation" + (" — escalated to human" if rec["is_fallback_escalation"] else "")
    st.markdown(f"#### {label}")
    st.write(rec["summary"])
    if rec.get("detailed_steps"):
        for step in rec["detailed_steps"]:
            st.markdown(f"- {step}")

    with st.expander("Full cited postmortem"):
        st.markdown(res["postmortem_markdown"])
