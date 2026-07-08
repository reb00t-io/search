"""Tests for the stateless /v1/chat/completions endpoint."""

import json
import os
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("LLM_BASE_URL", "http://fake-llm")
os.environ.setdefault("LLM_API_KEY", "test-llm-key")
os.environ.pop("API_KEY", None)

import src.main as main_module  # noqa: E402
from src.chat_completions import _last_user_message  # noqa: E402
from src.main import app  # noqa: E402


def mock_upstream(response_json: dict | None = None, *, status: int = 200,
                  capture: list | None = None):
    """Patch httpx.AsyncClient for a non-streaming upstream completion."""
    response_json = response_json or {"choices": [{"message": {"content": "ok"}}]}

    async def _post(url, headers=None, json=None):
        if capture is not None:
            capture.append({"url": url, "headers": headers, "body": json})
        resp = MagicMock()
        resp.status_code = status
        resp.content = __import__("json").dumps(response_json).encode()
        resp.headers = {"Content-Type": "application/json"}
        return resp

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.post = _post
        yield client

    return patch("src.main.httpx.AsyncClient", _client)


def mock_upstream_stream(chunks: list[bytes], *, capture: list | None = None):
    """Patch httpx.AsyncClient for a streaming upstream completion."""

    @asynccontextmanager
    async def _stream(method, url, headers=None, json=None):
        if capture is not None:
            capture.append({"url": url, "headers": headers, "body": json})

        async def aiter_raw():
            for chunk in chunks:
                yield chunk

        resp = MagicMock()
        resp.status_code = 200
        resp.aiter_raw = aiter_raw
        yield resp

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.stream = _stream
        yield client

    return patch("src.main.httpx.AsyncClient", _client)


@pytest.fixture()
def client():
    return app.test_client()


@pytest.fixture(autouse=True)
def disable_rag(monkeypatch):
    async def _no_rag(query):
        return None
    monkeypatch.setattr(main_module, "_rag_context_provider", _no_rag)


# ─── Validation & auth ───────────────────────────────────────────────────────

async def test_missing_messages_returns_400(client):
    resp = await client.post("/v1/chat/completions", json={})
    assert resp.status_code == 400


async def test_empty_messages_returns_400(client):
    resp = await client.post("/v1/chat/completions", json={"messages": []})
    assert resp.status_code == 400


async def test_auth_required_when_api_key_set(client):
    with patch("src.main.API_KEY", "secret"):
        resp = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 401


async def test_auth_passes_with_correct_key(client):
    with mock_upstream(), patch("src.main.API_KEY", "secret"):
        resp = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer secret"},
        )
    assert resp.status_code == 200


# ─── Proxying ────────────────────────────────────────────────────────────────

async def test_forwards_and_returns_upstream_response(client):
    captured = []
    with mock_upstream({"choices": [{"message": {"content": "Antwort"}}]}, capture=captured), \
            patch("src.main.LLM_BASE_URL", "http://fake-llm"), \
            patch("src.main.LLM_API_KEY", "test-llm-key"):
        resp = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Frage"}]},
        )
    assert resp.status_code == 200
    data = await resp.get_json()
    assert data["choices"][0]["message"]["content"] == "Antwort"
    assert captured[0]["url"] == "http://fake-llm/chat/completions"
    assert captured[0]["headers"]["Authorization"] == "Bearer test-llm-key"


async def test_model_is_overridden_with_backend_model(client):
    captured = []
    with mock_upstream(capture=captured):
        await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert captured[0]["body"]["model"] == main_module.LLM_MODEL


async def test_tools_are_forwarded_untouched(client):
    captured = []
    tools = [{"type": "function", "function": {"name": "read_books", "parameters": {}}}]
    with mock_upstream(capture=captured):
        await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "tools": tools},
        )
    assert captured[0]["body"]["tools"] == tools


async def test_upstream_error_status_is_passed_through(client):
    with mock_upstream({"error": "boom"}, status=500):
        resp = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 500


# ─── RAG injection ───────────────────────────────────────────────────────────

async def test_rag_context_inserted_before_last_user_message(client):
    async def fake_rag(query):
        return f"context for: {query}"

    captured = []
    with mock_upstream(capture=captured), patch.object(main_module, "_rag_context_provider", fake_rag):
        await client.post(
            "/v1/chat/completions",
            json={"messages": [
                {"role": "system", "content": "you are an agent"},
                {"role": "user", "content": "erste"},
                {"role": "assistant", "content": "antwort"},
                {"role": "user", "content": "zweite"},
            ]},
        )
    messages = captured[0]["body"]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "system", "user"]
    assert messages[3]["content"] == "context for: zweite"
    assert messages[4]["content"] == "zweite"


async def test_rag_disabled_via_flag_and_flag_stripped(client):
    async def fake_rag(query):
        return "context"

    captured = []
    with mock_upstream(capture=captured), patch.object(main_module, "_rag_context_provider", fake_rag):
        await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "rag": False},
        )
    body = captured[0]["body"]
    assert "rag" not in body
    assert [m["role"] for m in body["messages"]] == ["user"]


async def test_rag_failure_does_not_break_request(client):
    async def failing_rag(query):
        raise RuntimeError("qdrant down")

    with mock_upstream(), patch.object(main_module, "_rag_context_provider", failing_rag):
        resp = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200


# ─── Streaming ───────────────────────────────────────────────────────────────

async def test_streaming_relays_upstream_bytes_verbatim(client):
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hal"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    with mock_upstream_stream(chunks):
        resp = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        )
        body = await resp.get_data()
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    assert body == b"".join(chunks)


# ─── Helpers ─────────────────────────────────────────────────────────────────

class TestLastUserMessage:
    def test_string_content(self):
        messages = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                    {"role": "user", "content": "c"}]
        assert _last_user_message(messages) == (2, "c")

    def test_content_parts(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "part one"},
            {"type": "image_url", "image_url": {"url": "x"}},
            {"type": "text", "text": "part two"},
        ]}]
        assert _last_user_message(messages) == (0, "part one part two")

    def test_no_user_message(self):
        assert _last_user_message([{"role": "system", "content": "s"}]) == (-1, "")
