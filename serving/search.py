"""Hybrid search: BM25 + vector + RRF fusion."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from qdrant_client import QdrantClient, models

from indexing.bm25 import BM25Encoder
from indexing.embedder import embed_query
from indexing.indexer import COLLECTION_NAME

logger = logging.getLogger(__name__)


def _build_filter(lang: str | None, source: str | None) -> models.Filter | None:
    """Build Qdrant payload filter from query parameters."""
    conditions = []
    if lang and lang != "all":
        conditions.append(
            models.FieldCondition(key="language", match=models.MatchValue(value=lang))
        )
    if source and source != "all":
        conditions.append(
            models.FieldCondition(key="source", match=models.MatchValue(value=source))
        )
    if not conditions:
        return None
    return models.Filter(must=conditions)


def _extract_snippet(text: str, query: str, max_length: int = 300) -> str:
    """Extract a relevant snippet from text, highlighting query terms."""
    query_terms = set(re.findall(r"\b\w{2,}\b", query.lower()))
    sentences = re.split(r"(?<=[.!?])\s+", text)

    # Score each sentence by how many query terms it contains
    scored = []
    for sent in sentences:
        words = set(re.findall(r"\b\w{2,}\b", sent.lower()))
        overlap = len(query_terms & words)
        scored.append((overlap, sent))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Take the best sentences up to max_length
    snippet_parts = []
    total = 0
    for _, sent in scored:
        if total + len(sent) > max_length:
            if not snippet_parts:
                snippet_parts.append(sent[:max_length])
            break
        snippet_parts.append(sent)
        total += len(sent)

    snippet = " ... ".join(snippet_parts) if snippet_parts else text[:max_length]

    # Bold query terms in snippet (limit replacements to avoid bloat)
    for term in query_terms:
        snippet = re.sub(
            rf"\b({re.escape(term)})\b",
            r"**\1**",
            snippet,
            count=5,
            flags=re.IGNORECASE,
        )

    return snippet[:max_length + 100]  # hard cap with some room for bold markers


def search_bm25(
    client: QdrantClient,
    bm25: BM25Encoder,
    query: str,
    limit: int = 50,
    query_filter: models.Filter | None = None,
) -> list[models.ScoredPoint]:
    """BM25-only search using sparse vectors."""
    indices, values = bm25.encode_query(query)
    if not indices:
        return []
    return client.query_points(
        collection_name=COLLECTION_NAME,
        query=models.SparseVector(indices=indices, values=values),
        using="bm25",
        query_filter=query_filter,
        limit=limit,
    ).points


def search_vector(
    client: QdrantClient,
    query: str,
    limit: int = 50,
    query_filter: models.Filter | None = None,
    model_name: str = "intfloat/multilingual-e5-base",
) -> list[models.ScoredPoint]:
    """Vector-only search using dense embeddings."""
    query_vec = embed_query(query, model_name=model_name)
    return client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vec,
        using="dense",
        query_filter=query_filter,
        limit=limit,
    ).points


def search_hybrid(
    client: QdrantClient,
    bm25: BM25Encoder,
    query: str,
    limit: int = 50,
    query_filter: models.Filter | None = None,
    model_name: str = "intfloat/multilingual-e5-base",
) -> list[models.ScoredPoint]:
    """Hybrid search using Qdrant's built-in RRF prefetch + fusion."""
    query_vec = embed_query(query, model_name=model_name)
    sparse_indices, sparse_values = bm25.encode_query(query)

    prefetch = []

    # BM25 prefetch
    if sparse_indices:
        prefetch.append(
            models.Prefetch(
                query=models.SparseVector(indices=sparse_indices, values=sparse_values),
                using="bm25",
                limit=limit,
                filter=query_filter,
            )
        )

    # Dense prefetch
    prefetch.append(
        models.Prefetch(
            query=query_vec,
            using="dense",
            limit=limit,
            filter=query_filter,
        )
    )

    if not prefetch:
        return []

    return client.query_points(
        collection_name=COLLECTION_NAME,
        prefetch=prefetch,
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=limit,
    ).points


def search(
    client: QdrantClient,
    bm25: BM25Encoder,
    query: str,
    mode: str = "hybrid",
    lang: str | None = None,
    source: str | None = None,
    limit: int = 10,
    offset: int = 0,
    model_name: str = "intfloat/multilingual-e5-base",
) -> dict:
    """Execute a search and return formatted results.

    Args:
        mode: "hybrid", "bm25", or "vector"
    """
    start = time.time()
    query_filter = _build_filter(lang, source)

    # Fetch more than needed to handle offset
    fetch_limit = limit + offset

    if mode == "bm25":
        points = search_bm25(client, bm25, query, limit=fetch_limit, query_filter=query_filter)
    elif mode == "vector":
        points = search_vector(client, query, limit=fetch_limit, query_filter=query_filter, model_name=model_name)
    else:  # hybrid (default)
        points = search_hybrid(client, bm25, query, limit=fetch_limit, query_filter=query_filter, model_name=model_name)

    # Apply offset
    points = points[offset:][:limit]

    results = []
    for point in points:
        payload = point.payload or {}
        text = payload.get("text", "")
        snippet = _extract_snippet(text, query)
        results.append({
            "id": payload.get("doc_id", ""),
            "title": payload.get("title", ""),
            "url": payload.get("url", ""),
            "snippet": snippet,
            "language": payload.get("language", ""),
            "source": payload.get("source", ""),
            "score": round(point.score, 4) if point.score else 0,
            "timestamp": payload.get("timestamp", ""),
        })

    took_ms = round((time.time() - start) * 1000)

    return {
        "query": query,
        "mode": mode,
        "results": results,
        "total": len(results),
        "took_ms": took_ms,
    }
