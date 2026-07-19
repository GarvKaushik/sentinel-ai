"""ORM models.

One table: investigations — a record of every run (manual, catalogue, or alert).
The full ledger is stored as JSON so history can replay the exact result; the
flat columns (service, status, top_root_cause…) make the list view cheap.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Celery task id (async /alert). Null for synchronous runs.
    job_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)

    incident_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metric: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual|catalogue|alert

    # queued -> running -> done | failed  (only queued/running for async jobs)
    status: Mapped[str] = mapped_column(String(16), default="done", index=True)

    top_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)

    ledger_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    postmortem_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def summary_dict(self) -> dict:
        """Lightweight row for the history list (no heavy JSON payload)."""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "incident_id": self.incident_id,
            "service": self.service,
            "metric": self.metric,
            "trigger": self.trigger,
            "status": self.status,
            "top_root_cause": self.top_root_cause,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def detail_dict(self) -> dict:
        """Full record including the stored ledger + rendered postmortem."""
        data = self.summary_dict()
        data["ledger"] = self.ledger_json
        data["postmortem_markdown"] = self.postmortem_markdown
        data["error"] = self.error
        return data
