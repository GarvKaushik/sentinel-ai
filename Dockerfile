# Sentinel AI application image (light — hosted embeddings).
#
# Runs the FastAPI service that exposes the investigation pipeline. Qdrant,
# Postgres, and Redis are separate services (see docker-compose.yml); this
# image is only the app.
#
# Embeddings use the hosted Jina backend (EMBEDDING_BACKEND=jina), so this image
# installs NO torch and bakes NO model — it stays small (~250 MB) and builds in
# seconds. For an offline image with the local sentence-transformers backend,
# install requirements-local.txt instead (much heavier; not the deploy path).

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
