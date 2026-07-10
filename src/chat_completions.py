"""Stateless OpenAI-compatible /v1/chat/completions proxy.

For agent clients (e.g. tax-agent) that own their conversation history and
tool loop. In contrast to /v1/responses (stateful, server-side tools, web UI):

- No sessions: the client sends the full message list every request.
- No server-side tool execution: `tools` are forwarded to the LLM untouched;
  tool calls come back to the client, which executes them itself.
- Optional RAG: unless the request sets `"rag": false`, search results for
  the last user message are injected as a system message directly before it
  (same retrieval as the chat UI). The non-standard `rag` field is stripped
  before forwarding upstream.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from quart import Response, jsonify

logger = logging.getLogger(__name__)

UPSTREAM_TIMEOUT_SECONDS = 300


def _is_unauthorized(api_key: str, authorization: str) -> bool:
    return bool(api_key) and authorization != f"Bearer {api_key}"


def _last_user_message(messages: list[dict]) -> tuple[int, str]:
    """Return (index, text) of the last user message, or (-1, "").

    Handles both plain-string content and OpenAI content-part lists.
    """
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") != "user":
            continue
        content = messages[i].get("content")
        if isinstance(content, str):
            return i, content.strip()
        if isinstance(content, list):
            text = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            return i, text.strip()
        return i, ""
    return -1, ""


def _has_tool_history(messages: list[dict]) -> bool:
    return any(
        m.get("role") == "tool" or (m.get("role") == "assistant" and m.get("tool_calls"))
        for m in messages
    )


def _flatten_tool_history(messages: list[dict]) -> list[dict]:
    """Rewrite tool traffic as plain text.

    Some upstream models (observed with gpt-oss) keep emitting tool calls whenever the chat
    template contains tool history — even when the request defines no tools or
    sets tool_choice="none" — which surfaces as empty-content responses.
    Flattening assistant tool_calls and tool results into ordinary text
    reliably produces a text answer, so clients can force a final answer after
    their tool loop.
    """
    flat = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = "; ".join(
                f"{tc.get('function', {}).get('name', '?')}"
                f"({(tc.get('function', {}).get('arguments') or '')[:2000]})"
                for tc in m["tool_calls"]
            )
            content = (m.get("content") or "").strip()
            text = (content + "\n\n" if content else "") + f"[Ausgeführte Tool-Aufrufe: {calls}]"
            flat.append({"role": "assistant", "content": text})
        elif role == "tool":
            flat.append({
                "role": "user",
                "content": f"[Tool-Ergebnis {m.get('tool_call_id', '')}]\n{m.get('content') or ''}",
            })
        else:
            flat.append(m)
    # Without this the model tends to mimic the flattened "[Tool-Aufrufe: ...]"
    # notation instead of answering; tell it explicitly that research is over.
    flat.append({
        "role": "system",
        "content": "Die Recherche ist abgeschlossen. Antworte jetzt direkt und "
                   "vollständig als normaler Text; gib keine Tool-Aufrufe aus.",
    })
    return flat


async def _inject_rag_context(messages: list[dict], rag_context_provider) -> list[dict]:
    """Insert retrieved context as a system message before the last user message.

    Best-effort: any retrieval failure returns the messages unchanged.
    """
    index, query = _last_user_message(messages)
    if index < 0 or not query:
        return messages
    try:
        context = await rag_context_provider(query)
    except Exception:
        logger.exception("RAG context retrieval failed; continuing without context")
        return messages
    if not context:
        return messages
    return messages[:index] + [{"role": "system", "content": context}] + messages[index:]


async def post_chat_completions(
    *,
    body: dict,
    api_key: str,
    authorization: str,
    rag_context_provider,
    client_factory,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
):
    if _is_unauthorized(api_key, authorization):
        return jsonify({"error": "Unauthorized"}), 401

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "messages must be a non-empty list"}), 400

    request_body = dict(body)
    use_rag = request_body.pop("rag", True)
    if use_rag:
        messages = await _inject_rag_context(messages, rag_context_provider)

    # Text-only turns: either the client explicitly disabled tool calls
    # (tool_choice="none") or it sent tool history without defining tools.
    # In both cases the upstream model must not see tool machinery at all,
    # otherwise it keeps emitting tool calls (see _flatten_tool_history).
    if request_body.get("tool_choice") == "none" or (
        not request_body.get("tools") and _has_tool_history(messages)
    ):
        messages = _flatten_tool_history(messages)
        request_body.pop("tools", None)
        request_body.pop("tool_choice", None)

    request_body["messages"] = messages
    # The backend has exactly one configured model; the client's choice is
    # overridden so responses always reflect what actually ran.
    request_body["model"] = llm_model

    url = f"{llm_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {llm_api_key}",
        "Content-Type": "application/json",
    }

    if request_body.get("stream"):
        return Response(
            _relay_stream(request_body, url, headers, client_factory),
            content_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    async with client_factory(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        try:
            resp = await client.post(url, headers=headers, json=request_body)
        except Exception as exc:
            logger.warning("Upstream LLM request failed: %s", exc)
            return jsonify({"error": f"Upstream LLM request failed: {exc}"}), 502
        return Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("Content-Type", "application/json"),
        )


async def _relay_stream(request_body: dict, url: str, headers: dict, client_factory):
    """Relay the upstream SSE byte stream verbatim (no pacing, no re-chunking)."""
    try:
        async with client_factory(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
            async with client.stream(
                "POST",
                url,
                headers={**headers, "Accept": "text/event-stream", "Accept-Encoding": "identity"},
                json=request_body,
            ) as resp:
                if resp.status_code != 200:
                    error_body = (await resp.aread()).decode("utf-8", errors="replace")
                    yield _error_event(f"Upstream LLM returned {resp.status_code}: {error_body[:2000]}")
                    return
                async for chunk in resp.aiter_raw():
                    if chunk:
                        yield chunk
    except Exception as exc:
        logger.warning("Upstream LLM stream failed: %s", exc)
        yield _error_event(f"Upstream LLM stream failed: {exc}")


def _error_event(message: str) -> bytes:
    payload = json.dumps({"error": {"message": message}}, ensure_ascii=False)
    return f"data: {payload}\n\ndata: [DONE]\n\n".encode("utf-8")
