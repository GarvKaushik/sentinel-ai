"""
Query interface for the runbook RAG corpus.

Returns EvidenceObjects directly, not raw text — every retrieved chunk
comes with a source_ref pointing back to the exact doc section it came
from, so downstream agents (Root-Cause, Critic) can cite it and the
provenance validator can check it resolves.

Retrieval is hybrid by default: dense vector search (Qdrant, MiniLM
embeddings) fused with BM25 keyword search via Reciprocal Rank Fusion.
Dense search alone misses sections whose relevance is lexical rather than
semantic (e.g. an error string or metric name that appears verbatim in one
runbook section); BM25 alone misses paraphrases. RRF combines their rankings
without needing the two score scales to be comparable. Pass ``mode="dense"``
or ``mode="bm25"`` to isolate a single retriever — the evaluation harness
uses this to measure the recall each one contributes.
"""

from __future__ import annotations

import re

from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi

from app.retrieval.embeddings import Embedder, get_embedder
from app.retrieval.ingest import COLLECTION_NAME, get_qdrant_client
from app.schemas.evidence import EvidenceObject, SourceType

# RRF damping constant. 60 is the value from the original Cormack et al.
# paper and the de-facto default; it keeps any single ranker from dominating.
RRF_K = 60


def _tokenize(text: str) -> list[str]:
    """Lowercase word-token split. Deliberately simple — the corpus is small
    and BM25 is robust to naive tokenization."""
    return re.findall(r"\w+", text.lower())


def _fetch_corpus(client: QdrantClient) -> list[dict]:
    """Pull every chunk payload from Qdrant so BM25 can index the same corpus
    the dense index was built from. Qdrant stays the single source of truth —
    no re-reading the runbook files at query time."""
    points, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        limit=10_000,
        with_payload=True,
        with_vectors=False,
    )
    return [p.payload for p in points]


def _dense_ranking(query: str, client: QdrantClient, embedder: Embedder, limit: int) -> list[tuple[str, float]]:
    """Dense hits as (source_ref, cosine_score) in descending rank order."""
    query_vector = embedder.embed_query(query)
    hits = client.search(collection_name=COLLECTION_NAME, query_vector=query_vector, limit=limit)
    return [(h.payload["source_ref"], float(h.score)) for h in hits]


def _bm25_ranking(query: str, corpus: list[dict]) -> list[str]:
    """BM25 keyword hits as source_refs in descending rank order (all docs)."""
    tokenized = [_tokenize(c["text"]) for c in corpus]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(_tokenize(query))
    ranked = sorted(zip(corpus, scores), key=lambda pair: pair[1], reverse=True)
    return [c["source_ref"] for c, _ in ranked]


def _reciprocal_rank_fusion(ranked_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Fuse several ranked source_ref lists into one score per source_ref.

    RRF score = sum over lists of 1 / (k + rank), rank starting at 1. A doc
    ranked highly by either retriever scores well; a doc ranked highly by
    both scores best."""
    fused: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, source_ref in enumerate(ranked, start=1):
            fused[source_ref] = fused.get(source_ref, 0.0) + 1.0 / (k + rank)
    return fused


def search_runbooks(
    query: str,
    client: QdrantClient,
    embedder: Embedder,
    top_k: int = 3,
    produced_by: str = "retriever_agent",
    mode: str = "hybrid",
) -> list[EvidenceObject]:
    """Retrieve runbook sections as EvidenceObjects ready for the ledger.

    ``mode``:
      - ``"hybrid"`` (default): RRF over dense + BM25 rankings.
      - ``"dense"``: vector search only; ``confidence`` is the cosine score.
      - ``"bm25"``: keyword search only.

    For hybrid/bm25, ``confidence`` is a fused relevance score normalized so
    the top result is 1.0 — it is a ranking signal, not a cosine similarity.
    """
    corpus = _fetch_corpus(client)
    payload_by_ref = {c["source_ref"]: c for c in corpus}

    if mode == "dense":
        ranked = _dense_ranking(query, client, embedder, limit=top_k)
        chosen = [(ref, score) for ref, score in ranked]
    elif mode == "bm25":
        bm25_refs = _bm25_ranking(query, corpus)[:top_k]
        chosen = [(ref, 1.0) for ref in bm25_refs]
    elif mode == "hybrid":
        dense_refs = [ref for ref, _ in _dense_ranking(query, client, embedder, limit=len(corpus))]
        bm25_refs = _bm25_ranking(query, corpus)
        fused = _reciprocal_rank_fusion([dense_refs, bm25_refs])
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        top_score = ordered[0][1] if ordered else 1.0
        chosen = [(ref, score / top_score) for ref, score in ordered]
    else:
        raise ValueError(f"unknown retrieval mode: {mode!r} (expected 'hybrid', 'dense', or 'bm25')")

    evidence_items = []
    for source_ref, score in chosen:
        payload = payload_by_ref.get(source_ref)
        if payload is None:  # dense hit for a ref not in the scrolled corpus — skip defensively
            continue
        evidence_items.append(
            EvidenceObject(
                claim=f"Runbook guidance ({payload['section_title']}): {payload['text'][:200]}...",
                source_type=SourceType.DOC,
                source_ref=source_ref,
                confidence=max(0.01, min(1.0, float(score))),
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
    embedder = get_embedder()

    runbooks_dir = Path(__file__).resolve().parents[2] / "data" / "runbooks"
    ingest_runbooks(runbooks_dir, client, embedder)

    test_queries = [
        "error rate spiked right after a deploy, how do I find the guilty commit",
        "database queries are timing out and the connection pool looks full",
        "latency went up but no errors, what should I check",
    ]

    for q in test_queries:
        print(f"\nQUERY: {q}")
        for mode in ("dense", "bm25", "hybrid"):
            refs = [r.source_ref for r in search_runbooks(q, client, embedder, top_k=2, mode=mode)]
            print(f"  {mode:6s}: {refs}")
