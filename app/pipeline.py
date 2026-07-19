"""
Full investigation pipeline — the single orchestration entry point.

Chains the agents in order and returns the completed EvidenceLedger:

    Correlator (inside root-cause) -> Retriever -> Root-Cause -> Critic
    -> Recommendation -> Postmortem

Both the HTTP API (app/main.py) and any batch/eval caller should go through
run_investigation() rather than re-implementing the chain, so the ordering and
the citation-validation guarantees live in exactly one place.
"""

from __future__ import annotations

from app.schemas.evidence import EvidenceLedger
from app.schemas.scenario import IncidentScenario
from app.agents.root_cause import run_root_cause_investigation
from app.agents.critic import run_critic
from app.agents.recommendation import run_recommendation
from app.agents.postmortem import generate_postmortem


def run_investigation(scenario: IncidentScenario) -> EvidenceLedger:
    """Run the complete investigation for one incident and return the ledger
    with hypotheses, recommendation, and postmortem attached.

    Synchronous and LLM-backed: this makes several model calls and can take
    tens of seconds. The /alert endpoint runs it off the request path via a
    Celery worker (app/tasks.py); direct callers here run it inline."""
    ledger = run_root_cause_investigation(scenario)
    ledger = run_critic(ledger)
    ledger = run_recommendation(ledger)
    generate_postmortem(ledger, title=scenario.title)
    return ledger
