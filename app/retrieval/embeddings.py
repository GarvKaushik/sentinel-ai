"""Pluggable text embeddings.

Two backends, selected by the ``EMBEDDING_BACKEND`` env var:

  * ``local`` (default) — sentence-transformers ``all-MiniLM-L6-v2`` (384-dim),
    offline, no API key. Used for dev, tests, and CI.
  * ``jina``            — Jina AI's hosted embeddings API (``jina-embeddings-v3``,
    1024-dim). No torch/model in the image, so the deploy container is light.

Both backends expose the same interface (``embed_passages`` / ``embed_query`` /
``dim``), so ingest and search never know which one is active. Ingest-time and
query-time MUST use the same backend — the vectors have to live in the same
space — which is why both go through this one module.

``sentence-transformers`` (and torch) is imported lazily, only when the local
backend is actually constructed, so the Jina deploy image need not install it.
``httpx`` is a core dependency and imported normally (which also makes the Jina
backend easy to test by patching ``embeddings.httpx``).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Protocol

import httpx

# --- local backend ---
LOCAL_MODEL = "all-MiniLM-L6-v2"

# --- jina backend ---
JINA_API_URL = "https://api.jina.ai/v1/embeddings"
JINA_MODEL_DEFAULT = "jina-embeddings-v3"
JINA_DIM_DEFAULT = 1024
JINA_BATCH = 96  # Jina caps inputs per request; batch to stay well under it


class Embedder(Protocol):
    """The interface both backends satisfy."""

    dim: int

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """sentence-transformers backend. torch is imported lazily here so the Jina
    deploy image can skip it entirely."""

    def __init__(self, model_name: str = LOCAL_MODEL) -> None:
        from sentence_transformers import SentenceTransformer  # lazy: keeps torch out of the jina image

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, show_progress_bar=False)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode(text).tolist()


class JinaEmbedder:
    """Jina AI hosted embeddings, using task-specific encoding for retrieval
    (``retrieval.passage`` for documents, ``retrieval.query`` for queries — this
    measurably improves retrieval quality on jina-embeddings-v3)."""

    def __init__(self, model: str | None = None, dim: int | None = None) -> None:
        self.model = model or os.environ.get("JINA_MODEL", JINA_MODEL_DEFAULT)
        self.dim = dim or int(os.environ.get("JINA_DIM", JINA_DIM_DEFAULT))
        self._api_key = os.environ.get("JINA_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "JINA_API_KEY is not set (required for EMBEDDING_BACKEND=jina). "
                "Get a free key at https://jina.ai and put it in your .env."
            )

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), JINA_BATCH):
            batch = texts[start : start + JINA_BATCH]
            resp = httpx.post(
                JINA_API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model, "task": task, "dimensions": self.dim, "input": batch},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            # The API returns an "index" per item; sort by it so order matches input.
            for item in sorted(data, key=lambda d: d.get("index", 0)):
                out.append(item["embedding"])
        return out

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, task="retrieval.passage")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], task="retrieval.query")[0]


def backend_name() -> str:
    return os.environ.get("EMBEDDING_BACKEND", "local").lower()


@lru_cache(maxsize=None)
def get_embedder() -> Embedder:
    """Process-wide embedder for the configured backend (cached so the local
    model loads once, and the Jina key is validated once)."""
    backend = backend_name()
    if backend == "local":
        return LocalEmbedder()
    if backend == "jina":
        return JinaEmbedder()
    raise ValueError(f"unknown EMBEDDING_BACKEND: {backend!r} (expected 'local' or 'jina')")


def embedding_dim() -> int:
    """Vector size for the active backend — used to create the Qdrant collection
    at the right dimension."""
    return get_embedder().dim
