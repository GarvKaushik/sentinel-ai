# Sentinel AI — Autonomous Incident Investigation Platform

Portfolio project: multi-agent system that investigates production incidents,
generates evidence-grounded root-cause hypotheses, and drafts postmortems
with full source citation.

## What's built so far (Week 1, Day 1)

- ✅ `app/schemas/evidence.py` — the Evidence Object schema. Every agent
  downstream of retrieval reads/writes this, not free text. This is what
  makes citation enforcement and the eval harness possible.
- ✅ `app/schemas/scenario.py` — synthetic incident scenario schema
- ✅ `data/scenarios/scenario_001_bad_deploy.py` — first hand-crafted
  scenario, including a deliberate "red herring" decoy commit to test
  whether agents correlate by affected service, not just recency
- ✅ `app/main.py` — FastAPI skeleton with a smoke-test endpoint
- ✅ `docker-compose.yml` — Qdrant + Postgres + Redis, ready to run

## Setup

```bash
# 1. Start infra
docker compose up -d

# 2. Create venv + install deps
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Run the smoke test (no infra needed for this one)
python3 -c "
from app.schemas.evidence import EvidenceLedger, EvidenceObject, SourceType
ledger = EvidenceLedger(incident_id='test')
ledger.add_evidence(EvidenceObject(
    claim='test claim', source_type=SourceType.METRIC,
    source_ref='test:ref', confidence=0.9, produced_by='test'
))
print('Evidence count:', len(ledger.evidence))
"

# 4. Run the API
uvicorn app.main:app --reload
# then visit http://localhost:8000/smoke-test/evidence-ledger
```

## Next steps (in order — see the execution blueprint PDF for full detail)

1. **Write 2-3 runbook docs** in `data/runbooks/` — realistic incident
   playbooks (e.g. "API latency spike," "database connection pool exhaustion").
   These become your RAG corpus.
2. **Wire up Qdrant ingestion** — chunk + embed the runbooks, confirm you
   can retrieve relevant chunks for a test query with citations attached.
3. **Build the Correlator agent** — reads `scenario_001`, does simple
   anomaly localization (rule-based threshold check on the metrics is
   fine — don't over-engineer this with an LLM), outputs EvidenceObjects.
4. **Build the Root-Cause agent** — consumes EvidenceObjects, proposes
   ranked Hypotheses, each with `supporting_evidence_refs` that must
   resolve via `ledger.unresolved_refs()`.
5. **Build the Critic agent** — the falsification pass. Test it
   specifically against scenario_001's red herring: does it correctly
   avoid blaming the notifications commit just because it deployed
   more recently?
6. **Scale the scenario library** — once steps 3-5 work end-to-end on
   scenario_001, use an LLM to help generate 5-7 more scenario templates
   (resource exhaustion, dependency timeout, config drift, traffic spike,
   partial rollback), each with 10-15 parametrized variants.

## Design principle to hold onto throughout

No agent output should contain a claim that doesn't trace back to a real
`source_ref` in the EvidenceLedger. If you're ever tempted to let an agent
"just summarize" without a citation, that's the moment to stop and fix
the prompt or the schema instead. This constraint is annoying in week 1
and is the reason the whole project reads as credible in week 10.
