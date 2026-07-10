"""Tests for embedding API rate-limit handling."""

import httpx
import pytest

import indexing.embedder as embedder


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    return sleeps


def make_client(handler):
    return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))


def embedding_response(n):
    return {"data": [{"index": i, "embedding": [0.1] * embedder.DIMENSIONS} for i in range(n)]}


def test_429_is_retried_until_success(monkeypatch, no_sleep):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json=embedding_response(2))

    monkeypatch.setattr(embedder, "_get_client", lambda: make_client(handler))
    vectors = embedder._call_embeddings(["a", "b"])
    assert len(vectors) == 2
    assert calls["n"] == 3
    assert no_sleep[:2] == [3.0, 3.0]  # honored Retry-After


def test_429_never_falls_back_to_one_by_one(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429)

    monkeypatch.setattr(embedder, "_get_client", lambda: make_client(handler))
    with pytest.raises(httpx.HTTPStatusError):
        embedder._call_embeddings(["a", "b"])
    # exactly MAX_RETRIES batch attempts, no per-text explosion
    assert calls["n"] == embedder.MAX_RETRIES


def test_bad_input_isolated_one_by_one(monkeypatch):
    def handler(request):
        import json
        body = json.loads(request.content)
        if len(body["input"]) > 1 or body["input"] == ["bad"]:
            return httpx.Response(400)
        return httpx.Response(200, json=embedding_response(1))

    monkeypatch.setattr(embedder, "_get_client", lambda: make_client(handler))
    vectors = embedder._call_embeddings(["ok", "bad"])
    assert len(vectors) == 2
    assert vectors[1] == [0.0] * embedder.DIMENSIONS  # bad input -> zero vector


def test_transport_errors_retried(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=embedding_response(1))

    monkeypatch.setattr(embedder, "_get_client", lambda: make_client(handler))
    assert len(embedder._call_embeddings(["a"])) == 1
    assert calls["n"] == 2
