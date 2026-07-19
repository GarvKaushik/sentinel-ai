# Sentinel AI — Complete Project Context

> **Purpose of this file:** This is the self-contained working context for
> humans and LLM agents editing this repository. Treat the implementation as
> the source of truth. This guide records what exists today, how to run it,
> what must not be assumed, and the intended direction.

## 1. Project purpose and current status

Sentinel AI is an early-stage incident-investigation prototype. It accepts a
synthetic incident containing metrics, logs, and deployment history; gathers
structured evidence; retrieves relevant runbook guidance; asks LLM agents to
propose and falsify root-cause hypotheses; and produces a cited remediation
recommendation.

It is a portfolio prototype, not a hardened product (no authentication, no
multi-tenancy). But the platform pieces are now wired end to end: a full
investigation runs over HTTP, results are **persisted to PostgreSQL**, and the
live `/alert` path runs **asynchronously on a Redis + Celery queue** with a
worker process. All four backing services in Docker Compose — Qdrant, Postgres,
Redis, and Prometheus — are used by application code.

The key design principle is:

> No agent claim or action is accepted unless its `source_ref` resolves to an
> `EvidenceObject` already present in the incident ledger.

This provenance rule is more important than adding agents or dashboards. Keep
it intact in every future change.

## 2. Implemented architecture

```text
Synthetic IncidentScenario / JSON incident files
                 |
                 v
       Correlator agent (deterministic detection)
       metrics + ERROR/FATAL logs + deploy correlation
                 |
                 v
           EvidenceLedger (shared state)
                 ^
                 |             Markdown runbooks
                 |                    |
                 +--- Retriever <--- chunks + embeddings + Qdrant
                 |
                 v
     Root-Cause agent (LLM; cited ranked hypotheses)
                 |
                 v
          Critic agent (LLM; falsification/demotion)
                 |
                 v
 Recommendation agent (LLM; cited remediation or human escalation)
                 |
                 v
   Postmortem agent (deterministic; cited structured report)
```

The FastAPI service exposes the workflow above via `POST /investigate`
(see section 10), running it synchronously per request.

## 3. Repository map

| Path | What it contains | Current role |
| --- | --- | --- |
| `app/main.py` | FastAPI application | Health, incident catalogue, `/investigate*`, async `/alert`, and `/investigations` history routes; bootstraps the DB schema on startup. |
| `app/pipeline.py` | Investigation orchestrator | `run_investigation(scenario)` — the single place the agent chain + citation guarantees live. |
| `app/tasks.py` | Celery app + async task | `run_investigation_task` runs the pipeline on the worker off the request path; Redis is the broker. |
| `app/db/` | Persistence (SQLAlchemy) | `base.py` engine/session (opt-in via `DATABASE_URL`), `models.py` `Investigation` table, `repository.py` save/lifecycle/read. No-op when no DB is configured. |
| `app/ingestion/adapter.py` | Live-telemetry ingestion | Builds an `IncidentScenario` from Prometheus + the target's logs/deploys for `/alert`. |
| `app/schemas/evidence.py` | Evidence, hypothesis, recommendation, ledger models | Core provenance contract. |
| `app/schemas/scenario.py` | Synthetic incident input models | Scenario ground truth and source-data shapes. |
| `app/ingestion/loader.py` | JSON incident directory loader | Converts a `data/incidents/<id>/` folder to `IncidentScenario`. Backs the scenario catalogue. |
| `app/retrieval/chunking.py` | Markdown section chunker | Makes one citation-addressable chunk per `##` heading. |
| `app/retrieval/ingest.py` | Qdrant ingestion | Embeds runbook chunks and upserts them. |
| `app/retrieval/search.py` | Hybrid retrieval (dense + BM25 via RRF) | Returns runbook hits as `EvidenceObject` values; `mode` selects dense/bm25/hybrid. |
| `app/agents/correlator.py` | Rule-based evidence gathering + LLM summary | First investigation stage. |
| `app/agents/root_cause.py` | LLM hypothesis generation | Correlator → retrieval → cited hypotheses. |
| `app/agents/critic.py` | LLM falsification pass | Demotes or validates hypotheses. |
| `app/agents/recommendation.py` | LLM remediation generation | Uses only surviving hypotheses. |
| `app/agents/postmortem.py` | Deterministic report assembly | Builds a cited `PostmortemReport` from the completed ledger; no new LLM claims. |
| `eval/harness.py` | Single-investigation scoring | Non-LLM metrics for one finished investigation; returns `None` where a metric can't be honestly scored. |
| `eval/batch.py` | Catalogue-wide evaluation | Auto-discovers `data/incidents/*/`; offline correlator-coverage by default, optional full LLM batch. |
| `eval/test_harness.py`, `eval/test_batch.py` | Unit tests | Cover postmortem citation validity, harness scoring, catalogue breadth, and correlator coverage. |
| `app/llm/client.py` | Groq/OpenAI-compatible client wrapper | Reads `GROQ_API_KEY`; controls model requests. |
| `data/runbooks/` | Three Markdown runbooks | Initial RAG corpus. |
| `data/incidents/incident_001..007/` | JSON incident catalogue | 7 scenarios (one per failure category); the scenario library, loaded by `eval/batch.py`. |
| `data/scenarios/scenario_001_bad_deploy.py` | Python scenario fixture | Same incident as `incident_001`; imported by agent `__main__` smoke tests and `eval/test_harness.py`. |
| `app/db/test_repository.py`, `app/test_tasks.py` | Persistence + task tests | Run against SQLite / Celery eager mode — no Docker needed. |
| `ui/` | Streamlit demo cockpit | Inject faults, watch live telemetry, run investigations (async poll), browse persisted history. |
| `dummy/` | Fault-injecting target service | FastAPI app emitting one of 12 manufactured faults; Prometheus scrapes it. |
| `docker-compose.yml` | app, worker, cockpit, dummy, Qdrant, Postgres, Redis, Prometheus | The full one-command stack; all services are used. |
| `requirements.txt` | Current Python packages | Runtime dependencies. |
| `FUTURE_DEPENDENCIES.md` | Deferred packages and rationale | Roadmap, not installed functionality. |
| `README.md` | Original early project overview | Partly stale; use this guide for current context. |

`eval/` scores single investigations (`eval/harness.py`) and the whole
catalogue (`eval/batch.py`), over a 7-scenario JSON incident library. A Streamlit
cockpit (`ui/`), a Postgres-backed investigation history, and a Redis/Celery
worker are all present in the current tree.

## 4. Core data contracts

### Evidence object

`EvidenceObject` is the atomic unit exchanged between the correlator,
retriever, and reasoning agents.

| Field | Meaning |
| --- | --- |
| `claim` | A short factual statement. It must not be blank. |
| `source_type` | One of `metric`, `log`, `commit`, `doc`, or `incident`. |
| `source_ref` | A stable, resolvable citation key. |
| `confidence` | Number from 0.0 through 1.0. |
| `timestamp` | Optional event time. |
| `produced_by` | Agent/stage that created the object. |

Existing reference conventions:

```text
prometheus:<metric_name>:<service>:<ISO-8601 timestamp>
log:<service>:line:<line-number>
commit:<sha>
doc:<runbook-file-stem>#<section-slug>
```

`EvidenceLedger.resolve_ref()` returns a matching evidence object; 
`EvidenceLedger.unresolved_refs()` returns bad references. Do not replace these
checks with prompt-only instructions.

### Hypothesis

`Hypothesis` represents a possible root cause.

- `supporting_evidence_refs` must contain one or more resolvable references.
- `contradicting_evidence_refs` is populated by the critic.
- Valid status values in practice are `proposed`, `survived_critique`, and
  `demoted`; the schema also permits `confirmed` for a later human-approved
  workflow.
- Confidence represents evidence support, not incident severity.

### Recommendation

`Recommendation` has a summary, ordered detailed steps, supporting evidence,
optional risk notes, and `is_fallback_escalation`.

If a proposed action has no valid evidence references after validation, the
system assigns the fixed fallback: escalate to a human on-call engineer. Do
not silently emit an uncited operational action.

### Postmortem report

`PostmortemReport` holds a title, an executive summary, a timeline, an
optional root cause, and recommended actions — each section is a list of
`CitedStatement` (`text` + `supporting_evidence_refs`), never a plain string.
`validate_postmortem()` re-checks every statement's refs against the ledger
independently of how the report was built, and any failures are recorded in
`validation_errors` rather than silently dropped.

### Scenario and source-data models

`IncidentScenario` is synthetic-evaluation data. It contains real-looking
signals plus fields that must remain hidden from the investigation pipeline:

- `injected_root_cause`
- `root_cause_category`
- `CommitInfo.is_guilty_commit`
- `red_herrings`
- `expected_evidence_refs`

Those fields are valid only for test/evaluation fixtures. A future real
incident schema must not contain them.

## 5. Agent behavior and invariants

### Correlator — `app/agents/correlator.py`

This stage is mostly deterministic by design.

1. Groups metrics by service.
2. Uses the mean of the first half of each service's points as its baseline.
3. Detects the first point in the second half at least **3×** the baseline.
4. Writes a metric evidence object for that anomaly onset.
5. Writes evidence for every `ERROR` and `FATAL` log. `INFO` and `WARN` logs
   are ignored.
6. Infers a commit's service by looking for the service name (minus `svc-`) in
   `files_changed`.
7. Marks a commit as a plausible candidate only when it belongs to an
   anomalous service and lands from 0 to 300 seconds before anomaly onset.
8. Records every non-correlating commit as low-confidence (`0.15`) ruled-out
   evidence instead of dropping it. This negative evidence matters to the
   critic.
9. Calls the LLM only to write a 2–3 sentence dashboard summary. This summary
   is not an `EvidenceObject` and is never a valid citation.

Known constraints: anomaly grouping is by service, not `(service, metric)`;
commit-to-service inference is path-name matching; and log collection is not
time-windowed. These are intentionally simple prototype choices.

### Retriever — `app/retrieval/`

- `chunking.py` splits each runbook into chunks on `##` headings. It prepends
  the document title to each chunk and derives a readable citation slug.
- Embeddings come from the pluggable backend in `app/retrieval/embeddings.py`,
  selected by `EMBEDDING_BACKEND`: `local` (sentence-transformers
  `all-MiniLM-L6-v2`, 384-dim, offline) or `jina` (hosted `jina-embeddings-v3`,
  1024-dim, no torch — the light deploy path). `ingest.py` (re)creates the
  Qdrant `runbooks` collection at the active backend's vector size with cosine
  distance, so switching backends is safe.
- `search.py` retrieves hybrid by default: dense vector search fused with
  BM25 keyword search via Reciprocal Rank Fusion (RRF, `k=60`), converting each
  hit into `EvidenceObject(source_type="doc")`. `mode="dense"` / `mode="bm25"`
  isolate a single retriever; the eval harness uses these to compare them. BM25
  indexes the chunk corpus pulled from Qdrant, so Qdrant stays the single
  source of truth. For hybrid/bm25, an evidence object's `confidence` is a
  fused relevance score normalized so the top hit is 1.0 — a ranking signal,
  not a cosine similarity (dense mode still reports cosine).

There is no reranker (cross-encoder), architecture-document corpus, or
past-incident corpus yet.

Retrieval recall is scored at the **runbook-document level** (see
`eval.harness.runbook_of`): a scenario's expected `doc:` ref is satisfied if
any section of that runbook is retrieved. Within one correct runbook, several
sections (symptoms, root causes, diagnostics, remediation) are all legitimate
citations, so requiring the single section a scenario happens to label would
penalize retrieving a valid sibling — that measures label granularity, not
retriever quality.

Measured on the current catalogue (deterministic, LLM-free, `top_k=2`,
root-cause-stage query): dense **1.00**, hybrid **1.00**, BM25 alone **0.86**.
Hybrid does not beat dense on this small, semantically-rich corpus, but it is
more robust than either component alone: on the db-pool scenario BM25 alone
retrieves the wrong runbook (0.00) while hybrid preserves dense's correct hit
(1.00). That robustness — tracking the best retriever per query rather than
being dragged down by one — is why hybrid is the default. A cross-encoder
reranker remains the open hybrid sub-item; it would matter more as the corpus
grows past three runbooks.

### Root-Cause agent — `app/agents/root_cause.py`

1. Calls the correlator.
2. Uses up to four high-confidence ledger claims to form a runbook query.
3. Creates a Qdrant client and sentence-transformer model.
4. Retrieves two runbook chunks and appends them to the ledger.
5. Calls the stronger LLM model to return 2–4 hypotheses as JSON.
6. Strips unresolved citations; drops any hypothesis left with no valid
   supporting references; sorts the remainder by confidence.

`use_in_memory_qdrant=True` creates an empty in-memory Qdrant instance but
does **not** ingest runbooks automatically. It is not a working full-pipeline
shortcut unless the caller first populates that client. Normal runs require
the persistent `runbooks` collection to have been ingested.

### Critic agent — `app/agents/critic.py`

The critic receives all evidence and hypotheses, then asks an LLM to search
for contradictions. It validates every cited contradiction. It also enforces
the LLM's declared outcome in code:

- A `demoted` hypothesis cannot retain or gain confidence; if it does, code
  reduces it by 0.2 (minimum 0.0).
- A `survived` hypothesis with no valid contradiction cannot lose confidence
  merely because the LLM returned a lower number.
- Hypotheses are re-sorted by updated confidence.

This stage is meant to defeat recency bias, especially the unrelated
notifications commit in scenario 001.

### Recommendation agent — `app/agents/recommendation.py`

1. Selects the highest-confidence `survived_critique` hypothesis only.
2. Retrieves two remediation-oriented runbook chunks.
3. Asks the LLM for an action and cited steps.
4. Removes unresolved citations.
5. Falls back to human escalation if there is no surviving hypothesis or no
   valid recommendation citation.

The default `use_in_memory_qdrant=True` has the same empty-index limitation as
the root-cause agent.

### Postmortem agent — `app/agents/postmortem.py`

Deliberately deterministic: it turns an already-validated ledger into a
report without asking an LLM for any new factual claim. An LLM-written
narrative could replace this later only if it still produces `CitedStatement`
objects that pass `validate_postmortem`.

1. Picks the highest-confidence `survived_critique` hypothesis, if any, via
   `get_top_surviving_hypothesis`.
2. Builds the executive summary and root cause from that hypothesis
   (falling back to the first evidence item if no hypothesis survived).
3. Builds the timeline from every evidence item that has a `timestamp`,
   sorted chronologically.
4. Copies the recommendation's summary and steps into recommended actions
   only when it is not a fallback escalation.
5. Runs `validate_postmortem` and stores any unresolved-ref errors on the
   report instead of failing silently.

`render_markdown()` renders the report for a human, including inline
citations after each bullet; it prints a fixed fallback line per section
only when that section is genuinely empty.

## 6. Sample incident and data files

`scenario_001_bad_deploy` is the canonical test fixture:

- Service affected: `svc-payments`.
- Failure: error rate rises from approximately 0.4% to 12.8% at 14:33 UTC,
  followed by a `PaymentValidator` null-reference error.
- Guilty synthetic commit: `a1b2c3d`, a payments validation refactor deployed
  30 seconds before anomaly onset.
- Decoy: `e4f5g6h`, a notifications-only email-template commit deployed near
  the alert. Notifications metrics remain flat, so it should be ruled out.

The JSON version under `data/incidents/incident_001/` requires:

```text
metadata.json     scenario id, title, affected services, synthetic ground truth
metrics.json      [{timestamp, metric_name, value, service}, ...]
logs.json         [{timestamp, service, level, message, line_id}, ...]
deployments.json  [{sha, author, timestamp, message, files_changed, is_guilty_commit}, ...]
```

`app.ingestion.loader.load_incident(path)` reads that directory. The loader
currently expects synthetic ground-truth fields in `metadata.json`; it is not
a real production incident loader yet.

## 7. Runbooks and retrieval corpus

Current runbooks are:

- `runbook_deploy_error_spike.md`
- `runbook_api_latency_spike.md`
- `runbook_db_connection_pool.md`

When adding a runbook, use one `#` title and semantically coherent `##`
sections. Re-ingest afterward. The heading becomes part of the citation key,
so avoid changing headings casually after evaluation fixtures reference them.

## 8. Dependencies, infrastructure, and configuration

### Python

Use Python 3.12 where possible. The project currently pins:

- FastAPI and Uvicorn
- Pydantic v2
- `python-dotenv`
- Qdrant client
- OpenAI Python SDK, `httpx`
- SQLAlchemy + `psycopg` (Postgres), `celery[redis]` (async worker)

`requirements.txt` is the light **deploy** set (no torch). The local embedding
backend (`sentence-transformers` + torch) lives in `requirements-local.txt` for
dev/CI — `pip install -r requirements-local.txt`. See `requirements.txt` for
exact versions. `FUTURE_DEPENDENCIES.md` lists
packages that are deliberately deferred; do not install them merely because
they appear there.

### Docker services

`docker-compose.yml` starts:

| Service | Port | Used today |
| --- | --- | --- |
| app (this repo's `Dockerfile`) | 8000 | Yes. The Sentinel FastAPI service; on boot it waits for Qdrant, ingests runbooks, creates the DB schema, then serves. |
| worker (same image as app) | — | Yes. Celery worker; consumes jobs from Redis and runs the pipeline off the request path. |
| cockpit (`ui/Dockerfile`) | 8501 | Yes. The Streamlit demo cockpit — fault injection, live telemetry, async investigation, persisted history. |
| dummy (`dummy/Dockerfile`) | 9000 | Yes. The target service Sentinel observes; exposes `/metrics`, `/logs`, `/deploys`, fault injection. |
| Prometheus | 9090 | Yes. Scrapes the dummy; Sentinel's adapter queries it. |
| Qdrant | internal | Yes, for persistent runbook retrieval. Not published to the host. |
| PostgreSQL 16 | internal | Yes. Stores every investigation (`investigations` table); backs the history view. Not published to the host. |
| Redis 7 | internal | Yes. Celery broker (db 0) + result backend (db 1) for async `/alert`. Not published to the host. |

The whole demo starts with **one command** — `docker compose up --build` — and
opens at `http://localhost:8501` (the cockpit). Services reach each other by
compose DNS names: the Sentinel `app` uses `QDRANT_URL=http://qdrant:6333`,
`PROMETHEUS_URL=http://prometheus:9090`, `TARGET_URL=http://dummy:9000`, and reads
`GROQ_API_KEY`, `JINA_API_KEY`, and `POSTGRES_PASSWORD` from your local `.env`;
the `cockpit` uses internal URLs for its own calls but shows `localhost` links for
the browser. The app image is light (no torch — it uses the hosted Jina embedder),
and `docker-entrypoint.sh` runs the idempotent runbook ingestion before serving,
so a fresh `up` is fully populated with no manual steps. Because the image uses
the hosted embedder, the containerized stack needs a (free) `JINA_API_KEY`
alongside `GROQ_API_KEY`; bare-`uvicorn` local dev instead defaults to the offline
local backend via `requirements-local.txt`.

**Deploy hardening:** the datastores (Postgres, Redis, Qdrant) publish **no host
ports** — they're reachable only over the internal compose network, so they're
not exposed on a public VM. The Postgres password comes from `POSTGRES_PASSWORD`
in `.env` (no credential is committed); use a strong, URL-safe value on a real
deploy. Note Postgres only applies the password on first volume init, so changing
it later needs `docker compose down -v`. The app API and cockpit themselves have
no auth yet — put them behind a reverse proxy (or IP allowlist) for a real public
deploy.

### LLM provider

`app/llm/client.py` uses the OpenAI SDK against Groq's compatible endpoint:

- Base URL: `https://api.groq.com/openai/v1`
- Default model: `openai/gpt-oss-20b` for correlator summaries
- Root-cause, critic, recommendation model: `openai/gpt-oss-120b`
- Required environment variable: `GROQ_API_KEY`

Never put API keys in documentation, prompts, source files, test fixtures, or
commits. Keep `.env` local and ignored; rotate a key if it has been exposed.

## 9. Setup and exact commands

### Containerized (the deploy path)

The whole stack runs from one command; copy `.env.example` to `.env` and set
`GROQ_API_KEY` first:

```bash
cp .env.example .env      # then edit GROQ_API_KEY
docker compose up --build
```

This builds the app image, starts Qdrant, waits for it, ingests the runbooks,
and serves the API on `http://localhost:8000`. Then:

```bash
curl http://localhost:8000/incidents
curl -X POST http://localhost:8000/investigate/incident_001
```

### Local dev (venv)

From the repository root on Windows PowerShell:

```powershell
py -3.13 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements-local.txt   # base deps + the local (offline) embedder
docker compose up -d qdrant     # just the vector store
python -m app.retrieval.ingest
```

Create a local `.env` with `GROQ_API_KEY` before LLM-backed commands. Local dev
defaults to the offline `local` embedding backend (installed above); set
`EMBEDDING_BACKEND=jina` + `JINA_API_KEY` to use the hosted embedder instead.

Useful commands:

```powershell
# Serve the API (health, catalogue, and the /investigate routes)
uvicorn app.main:app --reload

# API smoke checks
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/incidents
# Full investigation over HTTP (needs Groq key + populated Qdrant)
Invoke-RestMethod -Method Post http://localhost:8000/investigate/incident_001

# In-memory retrieval smoke test; downloads/loads the embedding model as needed
python -m app.retrieval.search

# Correlator evidence collection plus LLM summary
python -m app.agents.correlator

# Correlator + retrieval + root-cause hypotheses
python -m app.agents.root_cause

# Full currently implemented chain: root cause + critic + recommendation
python -m app.agents.recommendation

# Full chain including the deterministic postmortem report
python -m app.agents.postmortem

# Offline correlator coverage over the whole 7-scenario catalogue (no Groq/Docker)
python -m eval.batch

# Unit tests (no Groq key or Docker required)
python -m unittest eval.test_harness eval.test_batch -v
```

The full LLM-backed batch (`eval.batch.evaluate_batch(run_full_pipeline)`) is
not a default command: it makes paid model calls for every scenario and needs
Qdrant populated. Call it from a script after setting `GROQ_API_KEY`.

The last four commands need a working Groq key. The root-cause,
recommendation, and postmortem commands also require that Qdrant has already
been populated by `python -m app.retrieval.ingest`.

## 10. API surface

| Route | Response |
| --- | --- |
| `GET /health` | `{"status": "ok"}` |
| `GET /incidents` | The built-in incident catalogue ids. |
| `POST /alert` | LIVE investigation: `{service, metric, window_minutes}` → **enqueues** the job on Redis and returns `{investigation_id, job_id, status: "queued", poll}` immediately; a Celery worker builds the incident from real Prometheus + logs/deploys and runs the pipeline. Alertmanager-shaped entry point. Falls back to synchronous when no DB/queue is configured. |
| `GET /investigations` | Recent investigation history (most recent first) from Postgres; `enabled` flag distinguishes "no history" from "no database". |
| `GET /investigations/{id}` | Full stored record: status, ledger, rendered postmortem, error. Also the **poll target** for async `/alert` jobs. |
| `POST /investigate` | Run a full investigation on an `IncidentScenario` in the request body (synchronous); persisted. |
| `POST /investigate/{incident_id}` | Run a full investigation on a catalogue incident (e.g. `incident_001`) (synchronous); persisted. |
| `GET /smoke-test/evidence-ledger` | Demonstrates valid and invalid citation resolution using hard-coded evidence. |

The `/investigate*` routes call `app.pipeline.run_investigation` synchronously and
return the completed ledger — hypotheses, recommendation, structured postmortem,
and a rendered `postmortem_markdown` — plus an `investigation_id`. `/alert` runs
the same pipeline asynchronously via `app/tasks.py`. Every run is written to the
`investigations` table (best-effort; the API still serves if Postgres is down).
All routes live in `app/main.py`.

## 11. Reliability and safety rules for future changes

1. Add evidence before reasoning. Every newly asserted fact needs a real
   `EvidenceObject` and citation.
2. Keep deterministic detection deterministic. Do not replace metric anomaly
   logic with an LLM without an evidence-backed and testable reason.
3. Validate model JSON and citations in code. Prompt wording is not a control.
4. Preserve negative evidence and critic demotion checks; they are essential
   to the project's value proposition.
5. Recommendations must remain human-in-the-loop. Do not add automatic
   production changes, rollbacks, or external writes without explicit
   approval, authorization, and auditability.
6. Never send synthetic ground truth (`injected_root_cause`, guilty flags,
   expected references) into a production-style investigation prompt.
7. Treat LLM model names, provider features, and API behavior as configurable
   external dependencies. Do not assume they remain stable.

## 12. Known gaps and prioritized roadmap

### Recently implemented

- Postmortem generation (`app/agents/postmortem.py`): deterministic,
  citation-validated, verified end-to-end against real Groq + Qdrant.
- Single-investigation evaluation (`eval/harness.py`): root-cause evidence
  recall, citation precision, retrieval recall, critic effectiveness — returns
  `None` with a recorded reason for any metric it cannot honestly score.
- Scenario library + batch runner (`data/incidents/incident_001..007`,
  `eval/batch.py`): 7 failure categories, discovered from JSON folders via
  `app.ingestion.loader.load_incident`. Offline correlator coverage is 100%
  across all 7; a full LLM batch has been run and scored.
- Retrieval recall is now measured: each scenario's `metadata.json` labels the
  runbook section a correct investigation should cite (a `doc:` ref in
  `expected_evidence_refs`), and the harness scores it separately from
  evidence-citation recall.
- Hybrid retrieval (`app/retrieval/search.py`): BM25 keyword search fused with
  dense vectors via RRF, now the default and selectable per mode. `eval/batch.py`
  gained `compare_retrieval_modes` / `evaluate_retrieval_recall` to score any
  retriever offline. Retrieval recall is measured at runbook-document level
  (`eval.harness.runbook_of`): dense 1.00, hybrid 1.00, BM25 alone 0.86. Hybrid
  ties dense but is more robust than either component (see the Retriever
  section). A cross-encoder reranker is the remaining hybrid sub-item.
- **Persistence (`app/db/`)**: every investigation is stored in a Postgres
  `investigations` table (SQLAlchemy; JSON ledger + flat summary columns).
  `GET /investigations` and `GET /investigations/{id}` expose the history; the
  cockpit shows it. Opt-in via `DATABASE_URL`, best-effort so the API never
  crashes when the DB is down. Verified on SQLite (`app/db/test_repository.py`).
- **Async execution (`app/tasks.py`)**: `/alert` enqueues on Redis and returns a
  job handle immediately; a Celery `worker` container runs the pipeline off the
  request path and writes the result to Postgres, which the caller polls.
  Verified in Celery eager mode (`app/test_tasks.py`).

Note on ref partitioning: `expected_evidence_refs` now mixes investigative
refs (metric/log/commit) and `doc:` refs. Correlator coverage and root-cause
evidence recall deliberately ignore `doc:` refs (the correlator/hypotheses
don't produce them); retrieval recall scores only the `doc:` refs.

### Remaining before presenting aggregate performance claims

1. **Critic effectiveness** is still unmeasured (`critic_effective` returns
   `n/a`): the only red-herring scenario (007) has its decoy ruled out upstream
   (correlator marks it out; root-cause never cites it), so the critic is never
   challenged. Measuring it needs a scenario where a decoy is genuinely
   tempting enough that a hypothesis cites it — deferred as a non-trivial,
   LLM-behavior-dependent design task, not a quick annotation.
2. A hallucination-rate / latency / cost metric is still not collected.

### Next platform upgrades

2. Hybrid retrieval: BM25 + dense + RRF is done; a cross-encoder reranker is
   the remaining sub-item. Bigger levers for retrieval recall on this corpus
   are more runbooks (close the config-drift gap) and multi-section / runbook-
   level relevance labels.
3. Real incident API and orchestration: incident submission, ledger/report
   retrieval, retries, timeouts, and status tracking are in place (async `/alert`
   + `/investigations`). Remaining: Alembic migrations (schema is currently
   `create_all` on startup) and richer job-status surfacing.
4. Tracing, prompt versioning, cost/latency telemetry, and a small dashboard
   showing the evidence and critic trace.
5. Read-only real integration, starting with GitHub commit metadata for a
   repository the user controls. Keep incident telemetry synthetic until the
   system is evaluated and access controls exist.

## 13. Guidance for an LLM taking over this repository

- Read this guide, then inspect the exact source files relevant to the task.
- Do not claim an item is implemented because it is listed in the roadmap or
  `FUTURE_DEPENDENCIES.md`.
- Prefer small composable changes that retain the `EvidenceLedger` contract.
- Add tests with any behavior change; the repository currently has no test
  suite, so introducing one is high value.
- Keep the README concise and update this file whenever architecture, data
  contracts, commands, dependencies, or operational status change.
- Before a full investigation run, make sure Qdrant is running and the
  `runbooks` collection is ingested.
