"""
Evidence Object schema.

This is the single most important data model in the project. Every agent
after the Correlator/Retriever consumes and produces EvidenceObjects instead
of free text. This is what makes citation enforcement, the Critic agent's
falsification pass, and the eval harness's citation-precision metric all
possible.

Design rule: no agent is allowed to make a claim without attaching a
source_ref that resolves to something real (a specific log line, a metric
timestamp, a commit SHA, or a doc chunk id). If it can't cite something
real, it doesn't get to say it.
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
    """A single, atomic, sourced claim about the incident."""

    claim: str = Field(..., description="Plain-language statement of what was observed")
    source_type: SourceType
    source_ref: str = Field(
        ...,
        description=(
            "A resolvable pointer to the underlying data, e.g. "
            "'prometheus:api_error_rate:2026-07-06T14:32:00Z', "
            "'log:svc-payments:line:4821', "
            "'commit:a1b2c3d', "
            "'doc:runbook-latency-spike#section-3'"
        ),
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    timestamp: Optional[datetime] = None
    produced_by: str = Field(..., description="Which agent generated this evidence, e.g. 'correlator_agent'")

    @field_validator("claim")
    @classmethod
    def claim_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("claim must not be empty")
        return v.strip()


class Hypothesis(BaseModel):
    """A candidate root cause, grounded in one or more EvidenceObjects."""

    hypothesis_id: str
    description: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence_refs: list[str] = Field(
        default_factory=list,
        description="source_ref values from EvidenceObjects that support this hypothesis",
    )
    contradicting_evidence_refs: list[str] = Field(
        default_factory=list,
        description="Filled in by the Critic agent during the falsification pass",
    )
    status: str = Field(
        default="proposed",
        description="proposed | survived_critique | demoted | confirmed",
    )
    critic_rationale: Optional[str] = None


class Recommendation(BaseModel):
    """Output of the Recommendation Agent — a grounded, actionable fix
    proposal. Every entry in supporting_evidence_refs must resolve in
    the ledger, same rule as Hypothesis. If no grounded recommendation
    can be made, the agent falls back to an explicit escalation rather
    than presenting an ungrounded fix — see app/agents/recommendation.py."""

    summary: str
    detailed_steps: list[str] = Field(default_factory=list)
    supporting_evidence_refs: list[str] = Field(default_factory=list)
    risk_notes: Optional[str] = None
    is_fallback_escalation: bool = Field(
        default=False,
        description="True when the agent couldn't ground a real fix and escalated instead",
    )


class EvidenceLedger(BaseModel):
    """
    The full, running collection of evidence + hypotheses for one incident
    investigation. Agents append to this rather than passing raw text
    between each other.
    """

    incident_id: str
    evidence: list[EvidenceObject] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    recommendation: Optional[Recommendation] = None

    def add_evidence(self, item: EvidenceObject) -> None:
        self.evidence.append(item)

    def resolve_ref(self, source_ref: str) -> Optional[EvidenceObject]:
        """Look up an EvidenceObject by its source_ref. Used by the
        provenance validator to check that a claim's citation actually
        exists before it's allowed into a report."""
        for e in self.evidence:
            if e.source_ref == source_ref:
                return e
        return None

    def unresolved_refs(self, refs: list[str]) -> list[str]:
        """Return any refs in the given list that do NOT resolve to a real
        EvidenceObject. An empty list back means every citation is valid."""
        return [r for r in refs if self.resolve_ref(r) is None]
