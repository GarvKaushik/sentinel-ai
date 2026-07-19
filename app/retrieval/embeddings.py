"""Turn text into vectors.

Two backends, picked by the EMBEDDING_BACKEND env var:
  - "local": a small model that runs on your machine (needs torch).
  - "jina":  Jina's hosted API (no torch, so the deploy image stays small).

Ingest and search must use the same backend, or the vectors won't match.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Protocol

import httpx

LOCAL_MODEL = "all-MiniLM-L6-v2"

JINA_API_URL = "https://api.jina.ai/v1/embeddings"
JINA_MODEL_DEFAULT = "jina-embeddings-v3"
JINA_DIM_DEFAULT = 1024
JINA_BATCH = 96  # max inputs to send per API call


class Embedder(Protocol):
    dim: int

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    def __init__(self, model_name: str = LOCAL_MODEL) -> None:
        # Imported here, not at the top, so the jina image doesn't need torch.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, show_progress_bar=False)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode(text).tolist()


class JinaEmbedder:
    def __init__(self, model: str | None = None, dim: int | None = None) -> None:
        self.model = model or os.environ.get("JINA_MODEL", JINA_MODEL_DEFAULT)
        self.dim = dim or int(os.environ.get("JINA_DIM", JINA_DIM_DEFAULT))
        self._api_key = os.environ.get("JINA_API_KEY")
        if not self._api_key:
            raise RuntimeError("JINA_API_KEY not set — needed for EMBEDDING_BACKEND=jina.")

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        # "task" tells Jina whether these are documents or a search query.
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
            # Sort by "index" so results line up with the inputs we sent.
            for item in sorted(resp.json()["data"], key=lambda d: d.get("index", 0)):
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
    # Cached so the local model loads only once.
    backend = backend_name()
    if backend == "local":
        return LocalEmbedder()
    if backend == "jina":
        return JinaEmbedder()
    raise ValueError(f"unknown EMBEDDING_BACKEND: {backend!r} (use 'local' or 'jina')")


def embedding_dim() -> int:
    # Used to size the Qdrant collection.
    return get_embedder().dim
