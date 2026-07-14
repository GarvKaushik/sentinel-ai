# Sentinel AI: Project Guide

Sentinel AI is a prototype autonomous incident-investigation system for
production services. Given an incident's metrics, logs, and deployment
history, it builds a cited evidence ledger, proposes root-cause hypotheses,
attempts to falsify them, and drafts a safe remediation recommendation.

The central design rule is **evidence before claims**: agents exchange
structured, resolvable evidence rather than unconstrained text. A hypothesis
or recommendation is accepted only when its cited `source_ref` values resolve
to evidence already stored in the investigation ledger.

## What is implemented

- A FastAPI service with health and evidence-schema smoke-test endpoints.
- Pydantic schemas for incidents, evidence, hypotheses, recommendations, and
  an evidence ledger.
- JSON ingestion for an incident data directory.
- Markdown-section chunking, embeddings, and dense-vector runbook retrieval
  with Qdrant.
- A deterministic correlator that detects metric anomalies, collects error
  logs, and correlates deployments to affected services.
- LLM-backed root-cause, critic, and recommendation agents with defensive
  JSON and citation validation.
- One synthetic incident with known ground truth and an intentionally
  misleading unrelated deployment.

## Architecture and investigation flow

```text
incident JSON + scenario data
            |
            v
      Correlator (rules)
      metrics, logs, deploys
            |
            v
      EvidenceLedger <----- Runbook retriever <----- Markdown runbooks
            |
            v
      Root-Cause agent (ranked, cited hypotheses)
            |
            v
      Critic agent (falsify or demote hypotheses)
            |
            v
      Recommendation agent (cited remediation or escalation)
```

### 1. Evidence ledger

`app/schemas/evidence.py` defines the core contract.

- `EvidenceObject` is one atomic claim, with a source type, confidence,
  producer, and a resolvable `source_ref` such as a Prometheus sample, log
  line, commit SHA, or runbook section.
- `Hypothesis` cites supporting evidence and can later contain contradicting
  evidence gathered by the critic.
- `Recommendation` cites the evidence that makes its action safe to propose.
- `EvidenceLedger` is the incident's shared state. Its `unresolved_refs()`
  function is the provenance gate used by downstream agents.

### 2. Correlator

`app/agents/correlator.py` intentionally uses rules for detection instead of
asking a model to interpret raw time series:

1. It calculates each service's baseline from the first half of its metric
   samples.
2. It marks the first later sample at least 3x that baseline as the anomaly
   onset.
3. It records all `ERROR` and `FATAL` logs as evidence.
4. It associates a commit with a service from its changed paths and treats it
   as plausible only when that service is anomalous and the commit preceded
   onset by no more than five minutes.
5. Commits that do not correlate are preserved as low-confidence, explicitly
   ruled-out evidence. This lets the critic reject tempting but irrelevant
   explanations.

The correlator may use the LLM only for a short dashboard summary. That
summary is narration, not evidence, and cannot be cited.

### 3. Runbook retrieval

Runbooks in `data/runbooks/` are split by `##` heading by
`app/retrieval/chunking.py`. Each section receives a human-readable reference
such as `doc:runbook-deploy-error-spike#diagnostic-steps`.

`app/retrieval/ingest.py` embeds those sections with
`all-MiniLM-L6-v2` (384 dimensions) and upserts them to the `runbooks` Qdrant
collection. `app/retrieval/search.py` performs dense cosine-similarity search
and returns the hits as `EvidenceObject` instances, so retrieval results enter
the ledger with provenance intact.

### 4. Root-cause, critic, and recommendation agents

- The Root-Cause agent turns high-confidence correlator findings into a
  runbook query, retrieves relevant guidance, asks an LLM for 2–4 ranked
  hypotheses, then strips unresolvable citations and drops hypotheses with no
  valid support.
- The Critic agent reviews every hypothesis adversarially. It can mark a
  hypothesis as `survived_critique` or `demoted`, cites contradictions, and
  enforces in code that a demoted hypothesis actually loses confidence.
- The Recommendation agent considers only the highest-confidence hypothesis
  that survived critique. It retrieves remediation guidance, validates the
  cited action, and otherwise returns an explicit human-escalation fallback
  instead of an ungrounded production change.

## Repository map

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI app and smoke-test endpoints. |
| `app/schemas/` | Pydantic contracts shared across the pipeline. |
| `app/ingestion/loader.py` | Loads an incident JSON directory into an `IncidentScenario`. |
| `app/retrieval/` | Runbook chunking, Qdrant ingestion, and semantic retrieval. |
| `app/agents/` | Correlator, root-cause, critic, and recommendation stages. |
| `app/llm/client.py` | OpenAI-compatible client configured for Groq. |
| `data/runbooks/` | RAG source documents for diagnostics and remediation. |
| `data/incidents/incident_001/` | JSON representation of the sample incident. |
| `data/scenarios/` | Python scenario fixture containing synthetic ground truth. |
| `docker-compose.yml` | Local Qdrant, PostgreSQL, and Redis services. |
| `FUTURE_DEPENDENCIES.md` | Deferred dependencies and planned capabilities. |

## Sample incident

`scenario_001_bad_deploy` models a payments error-rate spike. The useful
signals are a jump in `svc-payments` error rate, `PaymentValidator` null
reference errors, and a payments commit just before onset. A notifications
commit is deliberately close in time but belongs to a service whose metrics
stay flat. The expected conclusion is therefore a bad payments deployment,
not simply the most recent deployment.

The JSON fixture in `data/incidents/incident_001/` uses the same incident
shape and can be loaded with `load_incident()`.

## Prerequisites

- Python 3.12 is recommended for broad package-wheel compatibility.
- Docker Desktop, if using the persistent Qdrant service.
- A Groq API key for LLM-backed stages. Set `GROQ_API_KEY` in a local `.env`
  file or in the environment. Never commit that key.

## Setup and run

Create and activate a virtual environment, then install dependencies.

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Start local services when using persistent vector storage:

```powershell
docker compose up -d
```

Ingest the runbooks before a normal root-cause or recommendation run:

```powershell
python -m app.retrieval.ingest
```

Start the API:

```powershell
uvicorn app.main:app --reload
```

Then open `http://localhost:8000/docs`, or call:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/smoke-test/evidence-ledger
```

## Useful local checks

These commands run the individual stages from the repository root.

```powershell
# Test section chunking, embedding, and search using temporary in-memory Qdrant
python -m app.retrieval.search

# Run correlation and emit evidence; requires GROQ_API_KEY for its summary
python -m app.agents.correlator

# Full investigation; requires GROQ_API_KEY and an ingested Qdrant collection
python -m app.agents.root_cause

# Full investigation, falsification, and remediation recommendation
python -m app.agents.recommendation
```

## Current API

The HTTP API is intentionally minimal at this stage:

| Route | Result |
| --- | --- |
| `GET /health` | Returns `{"status": "ok"}`. |
| `GET /smoke-test/evidence-ledger` | Creates example evidence and demonstrates valid versus invalid citation resolution. |

The full investigation workflow is currently exposed through Python modules,
not an API endpoint or background job.

## Important current limitations

- The root-cause and recommendation modules query Qdrant but do not ingest
  documents automatically; run ingestion first for persistent Qdrant.
- LLM requests have no retry, timeout, tracing, or queue orchestration yet.
- PostgreSQL and Redis are available in Docker Compose but are not yet used by
  application code.
- Retrieval is dense-vector only; keyword search, reranking, and evaluation
  automation are planned rather than implemented.
- Ground-truth fields exist for synthetic evaluation. They must not be exposed
  to a real incident investigation pipeline.

## Extending the project

1. Add realistic runbooks under `data/runbooks/` with a single `#` title and
   `##` sections, then re-run ingestion.
2. Add incident directories with `metrics.json`, `logs.json`,
   `deployments.json`, and `metadata.json`, following `incident_001`.
3. Add synthetic scenarios with a known root cause and deliberate decoys for
   evaluation.
4. Preserve the ledger contract: add evidence first, then cite only exact
   `source_ref` values already in that ledger.
5. Add an orchestration/API layer only after the pipeline is reliable on a
   broader scenario set.

For the planned next dependencies and sequencing, see
[`FUTURE_DEPENDENCIES.md`](FUTURE_DEPENDENCIES.md).
