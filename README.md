# Sentinel AI

**Autonomous incident investigation — where every claim is cited to real evidence.**

🔗 **Live demo:** [garv-sentinel-ai.duckdns.org](https://garv-sentinel-ai.duckdns.org)
— the full stack running on a free-tier cloud VM behind HTTPS.

Sentinel AI takes a production incident (metrics, logs, deploy history), gathers
structured evidence, retrieves relevant runbook guidance, proposes and
*adversarially falsifies* root-cause hypotheses, and produces a grounded
remediation and a cited postmortem — end to end, over live telemetry.

Its defining rule: **no agent may make a claim without a `source_ref` that
resolves to real evidence.** This is enforced in code, not just asked for in a
prompt — unresolved citations are stripped and unsupported hypotheses dropped.

---

## Highlights

- **Multi-agent pipeline** with a hard citation-validation guarantee.
- **Runs on live telemetry**: an alert triggers an investigation built from
  real Prometheus metrics + a service's logs and deploys.
- **Async by design**: `/alert` enqueues on Redis; a Celery worker runs the
  ~20s pipeline off the request path and writes results to Postgres.
- **Hybrid RAG**: dense vectors + BM25 keyword search fused via Reciprocal Rank
  Fusion, over a Qdrant store.
- **Pluggable embeddings**: local (sentence-transformers) for dev, or the hosted
  Jina API for a light (~250 MB, no-torch) deploy image.
- **One-command stack**: 8 services via `docker compose up`, with health-gated
  startup and a Streamlit cockpit to drive the whole demo.
- **CI**: GitHub Actions runs lint + an offline test suite + Docker build checks.
- **Deployed for real**: live on an Oracle Cloud Always-Free ARM VM behind a
  Caddy reverse-proxy — auto HTTPS (Let's Encrypt) and per-IP rate limiting.

## How it works

```mermaid
flowchart LR
    A[Alert / Incident] --> B[Correlator]
    B --> C[Retriever]
    C --> D[Root-Cause]
    D --> E[Critic]
    E --> F[Recommendation]
    F --> G[Postmortem]
```

- **Correlator** — rule-based anomaly detection over metrics/logs/deploys (cheap
  and reliable; evidence grounded in arithmetic, not a model's guess).
- **Retriever** — hybrid search over runbooks; each hit cites the exact section.
- **Root-Cause** — LLM proposes ranked hypotheses, each citing real evidence.
- **Critic** — adversarial pass that tries to *break* each hypothesis.
- **Recommendation** — a grounded fix, or an explicit escalation if it can't
  ground one.
- **Postmortem** — deterministic, fully-cited report (no new facts invented).

For a **live** incident, an ingestion adapter turns an alert (`service` + metric
+ window) into the same incident shape the agents already speak, by querying
Prometheus and the target's `/logs` and `/deploys`.

## Tech stack

FastAPI · Celery + Redis · PostgreSQL · Qdrant · Prometheus · Streamlit ·
Groq LLMs (`gpt-oss-20b` / `120b`) · Jina / sentence-transformers embeddings ·
Docker Compose · GitHub Actions

**Services** (`docker compose`): `app` (API), `worker` (Celery), `cockpit`
(Streamlit UI), `dummy` (a fault-injecting target service), `prometheus`,
`qdrant`, `postgres`, `redis`.

## Quick start

```bash
cp .env.example .env
# set GROQ_API_KEY, JINA_API_KEY, and a strong POSTGRES_PASSWORD
docker compose up -d --build
```

Then open the cockpit at **http://localhost:8501** — inject one of 12
manufactured faults, watch the live telemetry move, run an investigation, and
browse the persisted history. The stack self-ingests the runbooks on first boot.

> **Heads-up for a fresh local clone.** The `caddy` service (the HTTPS
> reverse-proxy used for the public deploy) mounts `./Caddyfile`, which is
> **git-ignored** — so on a fresh clone that file doesn't exist and *only the
> `caddy` container will fail to start*. The rest of the stack (cockpit, app,
> worker, datastores) comes up fine and the cockpit is still at
> `localhost:8501`. To silence the failure, either:
> - `cp Caddyfile.example Caddyfile` before `docker compose up` (it'll try to
>   fetch a cert for the placeholder hostname — harmless locally), or
> - skip it entirely: `docker compose up -d --build --scale caddy=0`, or just
>   ignore the one failed container.
>
> Caddy is only needed for a public HTTPS deploy — see [Deployment](#deployment).

## The demo cockpit

One screen to drive everything: service status, one-click fault injection (and
heal), live Prometheus telemetry, run a full investigation on the live incident,
and a Postgres-backed history you can click into for the full cited report.

## API

| Route | What it does |
| --- | --- |
| `POST /alert` | Investigate a live incident (async — returns a job id to poll). |
| `POST /investigate` / `/investigate/{id}` | Run a full investigation (sync). |
| `GET /investigations` / `/investigations/{id}` | History + one run's full result. |
| `GET /incidents` · `GET /health` | Catalogue ids · liveness. |

## Testing

```bash
pip install -r requirements-local.txt   # base deps + local embedder + pytest
pytest                                    # offline: no keys, no services needed
```

## Project layout

```
app/
  agents/      correlator, root_cause, critic, recommendation, postmortem
  retrieval/   chunking, embeddings (pluggable), ingest, hybrid search
  ingestion/   live-telemetry adapter + incident loader
  db/          SQLAlchemy models + repository (investigation history)
  schemas/     the EvidenceObject / ledger contract
  tasks.py     Celery async task     pipeline.py   the orchestrator
  main.py      FastAPI app
eval/          non-LLM scoring harness + batch runner
dummy/         the fault-injecting target service
ui/            the Streamlit cockpit
```

## Deployment

The live demo runs the whole `docker compose` stack on a single **Oracle Cloud
Always-Free** ARM VM (`VM.Standard.A1.Flex`), fronted by a **Caddy**
reverse-proxy that terminates HTTPS (automatic Let's Encrypt certificate for a
free DuckDNS hostname) and rate-limits per client IP. Only ports 80/443 are
public; the app, cockpit, and every datastore stay on the internal Docker
network. Deploying elsewhere is the same `docker compose up -d --build` — see
[`Caddyfile.example`](Caddyfile.example) and [`caddy/Dockerfile`](caddy/Dockerfile)
for the proxy (the real `Caddyfile` is git-ignored, like `.env`).

## Status & notes

A portfolio project, not a hardened product. It runs as a single-node
`docker compose` stack; the datastores aren't exposed on the host and the
Postgres password comes from `.env`. The app and cockpit have no login of their
own — the public demo relies on the Caddy layer (HTTPS + rate limit) in front,
and you can drop in Caddy basic-auth or an IP allowlist if you need it locked
down further.
