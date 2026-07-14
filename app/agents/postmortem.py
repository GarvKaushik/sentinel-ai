"""Evidence-backed postmortem generation.

This first version is intentionally deterministic: it turns the already
validated investigation ledger into a structured report without asking an LLM
to make new factual claims. That gives Sentinel AI a reliable provenance
baseline. An LLM-written narrative can be added later only if it produces the
same ``CitedStatement`` contract and passes ``validate_postmortem``.
"""

from __future__ import annotations

from app.schemas.evidence import CitedStatement, EvidenceLedger, Hypothesis, PostmortemReport


def get_top_surviving_hypothesis(ledger: EvidenceLedger) -> Hypothesis | None:
    """Select the recommendation-safe hypothesis without importing LLM code."""
    survivors = [h for h in ledger.hypotheses if h.status == "survived_critique"]
    return max(survivors, key=lambda h: h.confidence) if survivors else None


def validate_postmortem(report: PostmortemReport, ledger: EvidenceLedger) -> list[str]:
    """Return provenance errors for every factual statement in a report."""
    errors: list[str] = []
    sections = {
        "executive_summary": report.executive_summary,
        "timeline": report.timeline,
        "recommended_actions": report.recommended_actions,
    }
    if report.root_cause is not None:
        sections["root_cause"] = [report.root_cause]

    for section, statements in sections.items():
        for index, statement in enumerate(statements, start=1):
            if not statement.supporting_evidence_refs:
                errors.append(f"{section}[{index}] has no supporting evidence refs")
                continue
            unresolved = ledger.unresolved_refs(statement.supporting_evidence_refs)
            if unresolved:
                errors.append(f"{section}[{index}] has unresolved refs: {unresolved}")
    return errors


def generate_postmortem(ledger: EvidenceLedger, title: str | None = None) -> PostmortemReport:
    """Create and attach an auditable report from a completed investigation."""
    top_hypothesis = get_top_surviving_hypothesis(ledger)
    timeline_evidence = sorted(
        (e for e in ledger.evidence if e.timestamp is not None),
        key=lambda e: e.timestamp,
    )

    executive_summary: list[CitedStatement] = []
    if top_hypothesis is not None:
        executive_summary.append(
            CitedStatement(
                text=(
                    f"The leading investigated cause is: {top_hypothesis.description} "
                    f"(confidence {top_hypothesis.confidence:.2f})."
                ),
                supporting_evidence_refs=top_hypothesis.supporting_evidence_refs,
            )
        )
    elif ledger.evidence:
        first_evidence = ledger.evidence[0]
        executive_summary.append(
            CitedStatement(text=first_evidence.claim, supporting_evidence_refs=[first_evidence.source_ref])
        )

    report = PostmortemReport(
        incident_id=ledger.incident_id,
        title=title or f"Incident postmortem: {ledger.incident_id}",
        executive_summary=executive_summary,
        timeline=[
            CitedStatement(
                text=f"{e.timestamp.isoformat()}: {e.claim}",
                supporting_evidence_refs=[e.source_ref],
            )
            for e in timeline_evidence
        ],
        root_cause=(
            CitedStatement(
                text=top_hypothesis.description,
                supporting_evidence_refs=top_hypothesis.supporting_evidence_refs,
            )
            if top_hypothesis is not None
            else None
        ),
    )

    if ledger.recommendation is not None and not ledger.recommendation.is_fallback_escalation:
        refs = ledger.recommendation.supporting_evidence_refs
        report.recommended_actions = [
            CitedStatement(text=ledger.recommendation.summary, supporting_evidence_refs=refs),
            *[
                CitedStatement(text=step, supporting_evidence_refs=refs)
                for step in ledger.recommendation.detailed_steps
            ],
        ]

    report.validation_errors = validate_postmortem(report, ledger)
    ledger.postmortem = report
    return report


def render_markdown(report: PostmortemReport) -> str:
    """Render a structured report without losing inline evidence references."""
    def render_statement(statement: CitedStatement) -> str:
        citations = ", ".join(f"`{ref}`" for ref in statement.supporting_evidence_refs)
        return f"- {statement.text}  \\n+  Evidence: {citations}"

    lines = [f"# {report.title}", "", f"Incident ID: `{report.incident_id}`", "", "## Executive summary"]
    lines.extend(render_statement(s) for s in report.executive_summary) or lines.append("- No supported summary available.")
    lines.extend(["", "## Timeline"])
    lines.extend(render_statement(s) for s in report.timeline) or lines.append("- No timestamped evidence available.")
    lines.extend(["", "## Root cause"])
    lines.extend([render_statement(report.root_cause)] if report.root_cause else ["- No surviving hypothesis available."])
    lines.extend(["", "## Recommended actions"])
    lines.extend(render_statement(s) for s in report.recommended_actions) or lines.append("- Escalate to an on-call human for a reviewed action plan.")

    if report.validation_errors:
        lines.extend(["", "## Provenance validation errors"])
        lines.extend(f"- {error}" for error in report.validation_errors)
    return "\n".join(lines) + "\n"
