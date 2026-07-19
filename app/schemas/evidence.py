"""The core data model: every claim is an EvidenceObject with a citation.

Agents pass these around instead of free text. The rule that makes the whole
thing trustworthy: no claim without a source_ref that points at something real
(a log line, a metric timestamp, a commit, a doc chunk). Can't cite it? Can't
say it.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class SourceType(str, Enum):
    METRIC = "metric"
    LOG = "log"
    COMMIT = "commit"
    DOC = "doc"          # runbooks, architecture docs
    INCIDENT = "incident"  # past postmortems retrieved via RAG


class EvidenceObject(BaseModel):
    """One sourced claim about the incident."""

    claim: str = Field(..., description="Plain-language statement of what was observed")
    source_type: SourceType
    source_ref: str = Field(
        ...,
        description="A pointer to the real data, e.g. 'log:svc-payments:line:4821' or 'commit:a1b2c3d'",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    timestamp: Optional[datetime] = None
    produced_by: str = Field(..., description="Which agent made this, e.g. 'correlator_agent'")

    @field_validator("claim")
    @classmethod
    def claim_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("claim must not be empty")
        return v.strip()


class Hypothesis(BaseModel):
    """A candidate root cause, backed by evidence."""

    hypothesis_id: str
    description: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence_refs: list[str] = Field(
        default_factory=list,
        description="source_refs that support this hypothesis",
    )
    contradicting_evidence_refs: list[str] = Field(
        default_factory=list,
        description="filled in by the Critic",
    )
    status: str = Field(
        default="proposed",
        description="proposed | survived_critique | demoted | confirmed",
    )
    critic_rationale: Optional[str] = None


class Recommendation(BaseModel):
    """The fix the Recommendation agent proposes.

    Same citation rule as everything else: every ref must resolve in the ledger.
    If it can't ground a real fix, it escalates to a human instead (see
    app/agents/recommendation.py)."""

    summary: str
    detailed_steps: list[str] = Field(default_factory=list)
    supporting_evidence_refs: list[str] = Field(default_factory=list)
    risk_notes: Optional[str] = None
    is_fallback_escalation: bool = Field(
        default=False,
        description="True when it couldn't ground a fix and escalated instead",
    )


class CitedStatement(BaseModel):
    """A sentence plus the evidence behind it.

    Postmortems are built from these (not plain strings) so we can check the
    citations before showing the report to anyone."""

    text: str = Field(..., min_length=1)
    supporting_evidence_refs: list[str] = Field(default_factory=list)


class PostmortemReport(BaseModel):
    """The final, evidence-backed incident report."""

    incident_id: str
    title: str
    executive_summary: list[CitedStatement] = Field(default_factory=list)
    timeline: list[CitedStatement] = Field(default_factory=list)
    root_cause: Optional[CitedStatement] = None
    recommended_actions: list[CitedStatement] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class EvidenceLedger(BaseModel):
    """All the evidence + hypotheses for one investigation. Agents append to
    this instead of passing text between each other."""

    incident_id: str
    evidence: list[EvidenceObject] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    recommendation: Optional[Recommendation] = None
    postmortem: Optional[PostmortemReport] = None

    def add_evidence(self, item: EvidenceObject) -> None:
        self.evidence.append(item)

    def resolve_ref(self, source_ref: str) -> Optional[EvidenceObject]:
        """Find the evidence with this source_ref, or None. Used to check a
        citation actually exists before a claim is allowed into a report."""
        for e in self.evidence:
            if e.source_ref == source_ref:
                return e
        return None

    def unresolved_refs(self, refs: list[str]) -> list[str]:
        """Which of these refs don't point at real evidence. Empty = all valid."""
        return [r for r in refs if self.resolve_ref(r) is None]
