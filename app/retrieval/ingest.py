"""
Ingest runbook chunks into Qdrant.

Embeddings come from the pluggable backend in ``app.retrieval.embeddings``
(local sentence-transformers by default, or the Jina API when
``EMBEDDING_BACKEND=jina``). The Qdrant collection is (re)created to match the
active backend's vector size, so switching backends is safe.

Run this as a script whenever you add/change runbooks:
    python -m app.retrieval.ingest
"""

from __future__ import annotations
import os
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from app.retrieval.chunking import chunk_all_runbooks
from app.retrieval.embeddings import Embedder, get_embedder

COLLECTION_NAME = "runbooks"

# Where Qdrant lives. Defaults to the local docker-compose port; inside the
# app container this is set to the compose service URL (http://qdrant:6333).
DEFAULT_QDRANT_URL = "http://localhost:6333"


def get_qdrant_client(in_memory: bool = False) -> QdrantClient:
    """Connect to Qdrant. in_memory=True runs an embedded instance for local
    testing without Docker; otherwise use QDRANT_URL (default localhost)."""
    if in_memory:
        return QdrantClient(location=":memory:")
    return QdrantClient(url=os.environ.get("QDRANT_URL", DEFAULT_QDRANT_URL))


def ensure_collection(client: QdrantClient, dim: int) -> None:
    """Create the collection at the given vector size. If it already exists with
    a different size (e.g. after switching embedding backends), drop and rebuild
    it so ingest can't fail on a dimension mismatch."""
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME in existing:
        current = client.get_collection(COLLECTION_NAME).config.params.vectors.size
        if current == dim:
            return
        client.delete_collection(COLLECTION_NAME)
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )


def ingest_runbooks(runbooks_dir: Path, client: QdrantClient, embedder: Embedder) -> int:
    """Chunk, embed, and upsert all runbooks. Returns number of chunks ingested."""
    chunks = chunk_all_runbooks(runbooks_dir)
    if not chunks:
        return 0

    texts = [c.text for c in chunks]
    vectors = embedder.embed_passages(texts)

    points = [
        PointStruct(
            id=i,
            vector=vectors[i],
            payload={
                "doc_id": chunks[i].doc_id,
                "section_slug": chunks[i].section_slug,
                "section_title": chunks[i].section_title,
                "text": chunks[i].text,
                "source_ref": chunks[i].source_ref,
            },
        )
        for i in range(len(chunks))
    ]

    ensure_collection(client, embedder.dim)
    client.upsert(collection_name=COLLECTION_NAME, points=points)
    return len(points)


if __name__ == "__main__":
    runbooks_dir = Path(__file__).resolve().parents[2] / "data" / "runbooks"
    client = get_qdrant_client(in_memory=False)  # expects docker compose up -d already run
    embedder = get_embedder()

    count = ingest_runbooks(runbooks_dir, client, embedder)
    print(f"Ingested {count} chunks from {runbooks_dir} into Qdrant collection '{COLLECTION_NAME}'")
