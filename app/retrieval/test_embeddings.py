"""Tests for the pluggable embedder — Jina backend + dispatch.

The Jina HTTP call is mocked (patch ``embeddings.httpx``), so these run with no
network, no API key beyond a fake one, and no torch. The local backend is the
pre-existing sentence-transformers path (unchanged logic) and is exercised by
the retrieval pipeline, so it isn't re-tested here to keep the suite fast.
"""

from __future__ import annotations

import pytest

from app.retrieval import embeddings


@pytest.fixture(autouse=True)
def _clear_cache():
    embeddings.get_embedder.cache_clear()
    yield
    embeddings.get_embedder.cache_clear()


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": self._data}


def test_embed_query_uses_query_task(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "test-key")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        captured["auth"] = headers["Authorization"]
        return _FakeResp([{"index": 0, "embedding": [0.1, 0.2, 0.3]}])

    monkeypatch.setattr(embeddings.httpx, "post", fake_post)

    vec = embeddings.JinaEmbedder(dim=3).embed_query("why is latency high")

    assert vec == [0.1, 0.2, 0.3]
    assert captured["url"] == embeddings.JINA_API_URL
    assert captured["auth"] == "Bearer test-key"
    assert captured["payload"]["task"] == "retrieval.query"
    assert captured["payload"]["input"] == ["why is latency high"]
    assert captured["payload"]["dimensions"] == 3


def test_embed_passages_batches_and_preserves_order(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "k")
    monkeypatch.setattr(embeddings, "JINA_BATCH", 2)  # force >1 batch for 3 inputs
    payloads = []

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        n = len(json["input"])
        # Return items scrambled to prove we re-sort by "index".
        data = [{"index": i, "embedding": [float(i)]} for i in range(n)]
        return _FakeResp(list(reversed(data)))

    monkeypatch.setattr(embeddings.httpx, "post", fake_post)

    vecs = embeddings.JinaEmbedder(dim=1).embed_passages(["a", "b", "c"])

    assert len(payloads) == 2  # batched as [a, b] then [c]
    assert [p["task"] for p in payloads] == ["retrieval.passage", "retrieval.passage"]
    # Order restored within each batch despite the scrambled response.
    assert vecs == [[0.0], [1.0], [0.0]]


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        embeddings.JinaEmbedder()


def test_get_embedder_dispatches_to_jina(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "jina")
    monkeypatch.setenv("JINA_API_KEY", "k")
    assert isinstance(embeddings.get_embedder(), embeddings.JinaEmbedder)


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "bogus")
    with pytest.raises(ValueError):
        embeddings.get_embedder()
