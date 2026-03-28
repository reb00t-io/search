"""Dense embedding via Privatemode API (qwen3-embedding-4b)."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

MODEL = "qwen3-embedding-4b"
DIMENSIONS = 1024
BATCH_SIZE = 32  # texts per API call
MAX_TEXT_CHARS = 24000  # ~8k tokens, well within 32k token limit
QUERY_INSTRUCT = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        base_url = os.environ["LLM_BASE_URL"]
        api_key = os.environ.get("LLM_API_KEY", "")
        _client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
        )
        logger.info("Embedding client initialized: %s model=%s dim=%d", base_url, MODEL, DIMENSIONS)
    return _client


def _truncate(text: str) -> str:
    """Truncate text to stay within the model's token limit."""
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[:MAX_TEXT_CHARS]


def _call_embeddings(inputs: list[str]) -> list[list[float]]:
    """Call the embeddings API. Returns vectors in input order."""
    client = _get_client()
    truncated = [_truncate(t) for t in inputs]
    try:
        resp = client.post(
            "/embeddings",
            json={
                "model": MODEL,
                "input": truncated,
                "dimensions": DIMENSIONS,
                "encoding_format": "float",
            },
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        # If batch fails, try one-by-one to isolate the bad input
        if len(truncated) > 1:
            logger.warning("Batch embedding failed (%s), retrying one-by-one", e.response.status_code)
            return _embed_one_by_one(truncated)
        logger.error("Embedding failed for text (len=%d): %s", len(truncated[0]), e)
        raise

    data = resp.json()["data"]
    data.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def _embed_one_by_one(texts: list[str]) -> list[list[float]]:
    """Fallback: embed texts individually, using zero vector for failures."""
    client = _get_client()
    results = []
    for text in texts:
        try:
            resp = client.post(
                "/embeddings",
                json={
                    "model": MODEL,
                    "input": [text],
                    "dimensions": DIMENSIONS,
                    "encoding_format": "float",
                },
            )
            resp.raise_for_status()
            results.append(resp.json()["data"][0]["embedding"])
        except Exception as e:
            logger.warning("Skipping text (len=%d): %s", len(text), e)
            results.append([0.0] * DIMENSIONS)
    return results


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document texts in batches."""
    all_embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        embeddings = _call_embeddings(batch)
        all_embeddings.extend(embeddings)
        if i + BATCH_SIZE < len(texts):
            logger.debug("Embedded batch %d-%d / %d", i, i + len(batch), len(texts))
    return all_embeddings


def embed_query(text: str) -> list[float]:
    """Embed a search query with retrieval instruction prefix."""
    result = _call_embeddings([f"{QUERY_INSTRUCT}{text}"])
    return result[0]


def get_embedding_dim() -> int:
    """Return embedding dimensionality."""
    return DIMENSIONS
