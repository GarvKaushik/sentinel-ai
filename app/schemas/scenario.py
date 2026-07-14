"""
Synthetic incident scenario schema.

Each scenario is a self-contained, ground-truth-labeled "fake production
incident" — realistic-looking metrics, logs, and deploy history, plus the
one thing real incidents never come with: the actual correct answer.

This is what your eval harness scores against. Root Cause Accuracy =
did the pipeline's top hypothesis match `injected_root_cause`.
"""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class MetricPoint(BaseModel):
    timestamp: str  # ISO8601
    metric_name: str
    value: float
    service: str


class LogEntry(BaseModel):
    timestamp: str
    service: str
    level: Literal["INFO", "WARN", "ERROR", "FATAL"]
    message: str
    line_id: str  # e.g. "log:svc-payments:line:4821" — matches EvidenceObject.source_ref format


class CommitInfo(BaseModel):
    sha: str
    author: str
    timestamp: str
    message: str
    files_changed: list[str]
    is_guilty_commit: bool = Field(
        default=False,
        description="Ground truth flag — is this the commit that actually caused the incident?",
    )


class IncidentScenario(BaseModel):
    scenario_id: str
    title: str
    services_affected: list[str]

    # The ground truth — never shown to the pipeline, only used for scoring
    injected_root_cause: str
    root_cause_category: Literal[
        "bad_deploy",
        "resource_exhaustion",
        "dependency_timeout",
        "config_drift",
        "db_connection_pool",
        "traffic_spike_no_bug",
        "partial_rollback",
        "red_herring",
    ]

    # The synthetic data itself
    metrics: list[MetricPoint]
    logs: list[LogEntry]
    deploy_history: list[CommitInfo]

    # For red-herring scenarios especially: a decoy that a naive system
    # (e.g. "blame the most recent deploy") would incorrectly pick
    red_herrings: list[str] = Field(default_factory=list)

    # What a correct investigation SHOULD end up citing — used for
    # Retrieval Recall scoring against the Retriever agent's output
    expected_evidence_refs: list[str] = Field(default_factory=list)
