#!/bin/sh
# Startup sequence for the app container:
#   1. wait until Qdrant is reachable
#   2. ingest runbooks (idempotent upsert — safe to re-run every boot)
#   3. start the API
set -e

echo "Waiting for Qdrant at ${QDRANT_URL:-http://localhost:6333} ..."
python - <<'PY'
import os, time
from qdrant_client import QdrantClient

url = os.environ.get("QDRANT_URL", "http://localhost:6333")
for _ in range(60):
    try:
        QdrantClient(url=url).get_collections()
        print("Qdrant is reachable.")
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit(f"Qdrant not reachable at {url} after 120s")
PY

echo "Ingesting runbooks ..."
python -m app.retrieval.ingest

echo "Starting Sentinel AI API on :8000 ..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
