from pathlib import Path
import json

from app.schemas.scenario import (
    IncidentScenario,
    MetricPoint,
    LogEntry,
    CommitInfo,
)


def load_incident(path: str| Path) -> IncidentScenario:
    path=Path(path)

    metrics = [
        MetricPoint(**m)
        for m in json.loads((path/"metrics.json").read_text())
    ]

    logs = [
        LogEntry(**l)
        for l in json.loads((path/"logs.json").read_text())
    ]

    deploys = [
        CommitInfo(**c)
        for c in json.loads((path/"deployments.json").read_text())
    ]

    meta = json.loads((path/"metadata.json").read_text())

    return IncidentScenario(
        scenario_id=meta["scenario_id"],
        title=meta["title"],
        services_affected=meta["services_affected"],
        metrics=metrics,
        logs=logs,
        deploy_history=deploys,
        # Unknown for real incidents
       injected_root_cause=meta["injected_root_cause"],
       root_cause_category=meta["root_cause_category"],

       red_herrings=meta.get("red_herrings", []),
       expected_evidence_refs=meta.get("expected_evidence_refs", []),
    )