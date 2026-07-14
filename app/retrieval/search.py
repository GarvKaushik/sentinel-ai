"""
Query interface for the runbook RAG corpus.

Returns EvidenceObjects directly, not raw text — every retrieved chunk
comes with a source_ref pointing back to the exact doc section it came
from, so downstream agents (Root-Cause, Critic) can cite it and the
provenance validator can check it resolves.

This is dense-only retrieval for now (Week 2 minimum). BM25 keyword
search + reciprocal rank fusion + a reranker come next as a fast
follow — see FUTURE_DEPENDENCIES.md. Don't add that complexity until
this baseline is retrieving sensibly on real queries.
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from app.retrieval.ingest import COLLECTION_NAME, EMBEDDING_MODEL, get_qdrant_client
from app.schemas.evidence import EvidenceObject, SourceType


def search_runbooks(
    query: str,
    client: QdrantClient,
    model: SentenceTransformer,
    top_k: int = 3,
    produced_by: str = "retriever_agent",
) -> list[EvidenceObject]:
    """Embed the query, search Qdrant, return results as EvidenceObjects
    ready to drop into an EvidenceLedger."""

    query_vector = model.encode(query).tolist()

    results = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=top_k,
    )

    evidence_items = []
    for hit in results:
        payload = hit.payload
        evidence_items.append(
            EvidenceObject(
                claim=f"Runbook guidance ({payload['section_title']}): {payload['text'][:200]}...",
                source_type=SourceType.DOC,
                source_ref=payload["source_ref"],
                confidence=float(hit.score),  # cosine similarity score, 0-1
                produced_by=produced_by,
            )
        )
    return evidence_items


if __name__ == "__main__":
    # Manual smoke test — run against in-memory Qdrant so it works without
    # Docker, using it purely to confirm retrieval quality on real queries.
    from pathlib import Path
    from app.retrieval.ingest import ingest_runbooks

    client = get_qdrant_client(in_memory=True)
    model = SentenceTransformer(EMBEDDING_MODEL)

    runbooks_dir = Path(__file__).resolve().parents[2] / "data" / "runbooks"
    ingest_runbooks(runbooks_dir, client, model)

    test_queries = [
        "error rate spiked right after a deploy, how do I find the guilty commit",
        "database queries are timing out and the connection pool looks full",
        "latency went up but no errors, what should I check",
    ]

    for q in test_queries:
        print(f"\nQUERY: {q}")
        results = search_runbooks(q, client, model, top_k=2)
        for r in results:
            print(f"  [{r.confidence:.3f}] {r.source_ref}")
