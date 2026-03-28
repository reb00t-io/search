"""Dense embedding via Privatemode API (qwen3-embedding-4b)."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

MODEL = "qwen3-embedding-4b"
DIMENSIONS = 1024
BATCH_SIZE = 64  # max texts per API call
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


def _call_embeddings(inputs: list[str]) -> list[list[float]]:
    """Call the embeddings API. Returns vectors in input order."""
    client = _get_client()
    resp = client.post(
        "/embeddings",
        json={
            "model": MODEL,
            "input": inputs,
            "dimensions": DIMENSIONS,
            "encoding_format": "float",
        },
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    # Sort by index to guarantee order
    data.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in data]


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
