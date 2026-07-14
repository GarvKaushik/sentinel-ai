# Future dependencies — add these back in ONLY when you reach the
# matching step. Don't install ahead of need; it's what caused the
# Python 3.14 wheel-compatibility mess.

## Step: Runbooks + Qdrant ingestion (RAG corpus)
qdrant-client==1.11.1
sentence-transformers==3.1.1     # pulls in torch — heaviest install in the project
rank-bm25==0.2.2

## Step: Correlator / Root-Cause / Critic agents (LangGraph orchestration)
langgraph==0.2.39
langchain-core==0.3.10
anthropic==0.36.0                 # or openai==1.51.0, depending which LLM you use
tenacity==9.0.0                   # retry logic for LLM calls

## Step: Postgres storage (investigation records, postmortem reports)
sqlalchemy==2.0.35
psycopg[binary]==3.2.3            # NOT psycopg2-binary — see note below
alembic==1.13.3

## Step: Async job queue (once you're running full investigations, not just testing)
celery==5.4.0
redis==5.1.1

## Step: LLMOps / tracing (Week 8 in the plan)
langfuse==2.51.0

## Step: numeric utilities (only if/when something actually needs it directly)
numpy>=2.1.0

---
NOTE on psycopg: use `psycopg[binary]` (v3), not `psycopg2-binary`. The v3
package has current wheels for newer Python releases; v2 lags behind and
is what caused the original Windows build error.

NOTE on Python version: if sentence-transformers/torch gives you the same
"building from source" trouble numpy did, that's your signal to switch
this project to Python 3.12 in a fresh venv rather than patch package by
package — 3.12 has full wheel support across the ML stack, 3.14 doesn't yet.
