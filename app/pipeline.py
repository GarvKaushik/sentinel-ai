"""The whole investigation, start to finish.

Runs the agents in order and returns the finished ledger:

    Correlator -> Retriever -> Root-Cause -> Critic -> Recommendation -> Postmortem

Everything (the API, eval, the worker) goes through run_investigation(), so the
order and the citation checks live in one place.
"""

from __future__ import annotations

from app.schemas.evidence import EvidenceLedger
from app.schemas.scenario import IncidentScenario
from app.agents.root_cause import run_root_cause_investigation
from app.agents.critic import run_critic
from app.agents.recommendation import run_recommendation
from app.agents.postmortem import generate_postmortem


def run_investigation(scenario: IncidentScenario) -> EvidenceLedger:
    """Run the full investigation for one incident and return the ledger with
    hypotheses, recommendation, and postmortem filled in.

    Takes tens of seconds (several LLM calls). /alert runs this on a worker;
    other callers run it inline."""
    ledger = run_root_cause_investigation(scenario)
    ledger = run_critic(ledger)
    ledger = run_recommendation(ledger)
    generate_postmortem(ledger, title=scenario.title)
    return ledger
