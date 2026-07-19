# Sentinel AI app image — light (hosted embeddings, no torch).
#
# Just the FastAPI app; Qdrant, Postgres, and Redis are separate services.
# Uses the hosted Jina embedder, so it installs no torch and bakes no model
# (~250 MB, builds in seconds). For an offline image with the local embedder,
# install requirements-local.txt instead.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY data ./data
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

# Entrypoint waits for Qdrant, ingests runbooks (idempotent), then serves.
ENTRYPOINT ["./docker-entrypoint.sh"]
