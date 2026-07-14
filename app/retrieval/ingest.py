"""
Ingest runbook chunks into Qdrant.

Uses a small, fast sentence-transformers model (all-MiniLM-L6-v2, 384-dim)
— plenty good enough for runbook-scale retrieval and fast to run locally
without a GPU. Swap for a larger model later only if retrieval quality
on your eval set actually demands it.

Run this as a script whenever you add/change runbooks:
    python -m app.retrieval.ingest
"""

from __future__ import annotations
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer

from app.retrieval.chunking import chunk_all_runbooks

COLLECTION_NAME = "runbooks"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


def get_qdrant_client(in_memory: bool = False) -> QdrantClient:
    """
    in_memory=True is for local testing without Docker running.
    In normal dev, use in_memory=False to talk to the Qdrant container
    from docker-compose (localhost:6333).
    """
    if in_memory:
        return QdrantClient(location=":memory:")
    return QdrantClient(url="http://localhost:6333")


def ensure_collection(client: QdrantClient) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )


def ingest_runbooks(runbooks_dir: Path, client: QdrantClient, model: SentenceTransformer) -> int:
    """Chunk, embed, and upsert all runbooks. Returns number of chunks ingested."""
    chunks = chunk_all_runbooks(runbooks_dir)
    if not chunks:
        return 0

    texts = [c.text for c in chunks]
    embeddings = model.encode(texts, show_progress_bar=False)

    points = [
        PointStruct(
            id=i,
            vector=embeddings[i].tolist(),
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

    ensure_collection(client)
    client.upsert(collection_name=COLLECTION_NAME, points=points)
    return len(points)


if __name__ == "__main__":
    runbooks_dir = Path(__file__).resolve().parents[2] / "data" / "runbooks"
    client = get_qdrant_client(in_memory=False)  # expects docker compose up -d already run
    model = SentenceTransformer(EMBEDDING_MODEL)

    count = ingest_runbooks(runbooks_dir, client, model)
    print(f"Ingested {count} chunks from {runbooks_dir} into Qdrant collection '{COLLECTION_NAME}'")
