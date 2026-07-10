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
    truncated = [_truncate(t) for t in inputs]
    try:
        resp = _post_with_backoff(truncated)
    except httpx.HTTPStatusError as e:
        # A non-retryable 4xx on a batch usually means one bad input —
        # isolate it one-by-one. Rate limits (429) never reach this branch:
        # they are retried with backoff and raise only when exhausted, so a
        # throttled API can never degrade into 32x more single requests.
        if len(truncated) > 1 and e.response.status_code not in RETRY_STATUS:
            logger.warning("Batch embedding failed (%s), retrying one-by-one", e.response.status_code)
            return _embed_one_by_one(truncated)
        logger.error("Embedding failed (%d texts): %s", len(truncated), e)
        raise

    data = resp.json()["data"]
    data.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in data]


# Retryable statuses: rate limit + transient upstream errors.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 8
MAX_BACKOFF_SECONDS = 60.0


def _post_with_backoff(inputs: list[str]) -> httpx.Response:
    """POST /embeddings, retrying 429/5xx with exponential backoff.

    Honors the Retry-After header when the API sends one, otherwise doubles
    the wait (1s, 2s, ... capped at 60s). Raises after MAX_RETRIES so the
    indexing loop stops loudly instead of writing bad vectors.
    """
    import time

    client = _get_client()
    payload = {
        "model": MODEL,
        "input": inputs,
        "dimensions": DIMENSIONS,
        "encoding_format": "float",
    }
    backoff = 1.0
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.post("/embeddings", json=payload)
            if resp.status_code not in RETRY_STATUS:
                resp.raise_for_status()
                return resp
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
            wait = min(wait, MAX_BACKOFF_SECONDS)
            logger.info("Embeddings API returned %d, retrying in %.0fs (attempt %d/%d)",
                        resp.status_code, wait, attempt, MAX_RETRIES)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in RETRY_STATUS:
                raise
            last_exc = e
        except httpx.TransportError as e:
            wait = min(backoff, MAX_BACKOFF_SECONDS)
            logger.info("Embeddings API transport error (%s), retrying in %.0fs (attempt %d/%d)",
                        e, wait, attempt, MAX_RETRIES)
            last_exc = e
        if attempt < MAX_RETRIES:
            time.sleep(wait)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
    raise last_exc  # type: ignore[misc]


def _embed_one_by_one(texts: list[str]) -> list[list[float]]:
    """Fallback for a failed batch: embed texts individually to isolate bad
    inputs. Only genuinely bad inputs (non-retryable 4xx) are skipped with a
    zero vector; rate limits and transient errors keep retrying/raise."""
    results = []
    for text in texts:
        try:
            resp = _post_with_backoff([text])
            results.append(resp.json()["data"][0]["embedding"])
        except httpx.HTTPStatusError as e:
            if e.response.status_code in RETRY_STATUS:
                raise  # exhausted retries on a transient error — stop loudly
            logger.warning("Skipping bad input (len=%d): %s", len(text), e)
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
