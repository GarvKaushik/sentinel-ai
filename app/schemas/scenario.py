"""A fake incident, with the answer key.

Each scenario has realistic-looking metrics/logs/deploys plus the one thing real
incidents don't come with: the actual root cause. The eval harness scores the
pipeline's top hypothesis against `injected_root_cause`.
"""

from __future__ import annotations
from typing import Literal, Optional
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
        description="the commit that actually caused it (ground truth)",
    )


class IncidentScenario(BaseModel):
    scenario_id: str
    title: str
    services_affected: list[str]

    # The answer key — never shown to the pipeline, only used by eval to score.
    # None for real incidents (the ingestion adapter has no label to give).
    injected_root_cause: Optional[str] = None
    root_cause_category: Optional[
        Literal[
            "bad_deploy",
            "resource_exhaustion",
            "dependency_timeout",
            "config_drift",
            "db_connection_pool",
            "traffic_spike_no_bug",
            "partial_rollback",
            "red_herring",
        ]
    ] = None

    # the data
    metrics: list[MetricPoint]
    logs: list[LogEntry]
    deploy_history: list[CommitInfo]

    # a decoy a naive system would wrongly blame (for red-herring scenarios)
    red_herrings: list[str] = Field(default_factory=list)

    # what a correct investigation should cite — used to score retrieval recall
    expected_evidence_refs: list[str] = Field(default_factory=list)
