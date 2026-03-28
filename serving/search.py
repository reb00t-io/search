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
) -> list[models.ScoredPoint]:
    """Vector-only search using dense embeddings."""
    query_vec = embed_query(query)
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
) -> list[models.ScoredPoint]:
    """Hybrid search using Qdrant's built-in RRF prefetch + fusion."""
    query_vec = embed_query(query)
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


def _base_doc_id(doc_id: str) -> str:
    """Extract base document ID by stripping the chunk index.

    'wiki:de:12345:0' -> 'wiki:de:12345'
    'arxiv:2301.07041:0' -> 'arxiv:2301.07041'

    Doc IDs have the form source:...:chunk_index. We strip the last
    segment only if the remaining ID has at least 3 colon-separated parts
    (source + lang/id + page_id) for wiki, or 2 parts for arxiv.
    """
    parts = doc_id.split(":")
    if len(parts) >= 3 and parts[-1].isdigit():
        return ":".join(parts[:-1])
    return doc_id


def _deduplicate_to_docs(points: list, query: str) -> list[dict]:
    """Group chunks by document, return one result per doc.

    For each document, picks the best-scoring chunk as the snippet.
    Documents with more matching chunks get a score boost (log scale).
    """
    import math

    docs: dict[str, dict] = {}  # base_doc_id -> best result so far

    for point in points:
        payload = point.payload or {}
        doc_id = payload.get("doc_id", "")
        base_id = _base_doc_id(doc_id)
        score = point.score if point.score else 0

        if base_id in docs:
            docs[base_id]["_chunk_count"] += 1
            # Keep the chunk with the best score as the snippet
            if score > docs[base_id]["_best_score"]:
                docs[base_id]["snippet"] = _extract_snippet(payload.get("text", ""), query)
                docs[base_id]["_best_score"] = score
        else:
            docs[base_id] = {
                "id": base_id,
                "title": payload.get("title", ""),
                "url": payload.get("url", ""),
                "full_text_url": payload.get("full_text_url", ""),
                "content_type": payload.get("content_type", "full_text"),
                "snippet": _extract_snippet(payload.get("text", ""), query),
                "language": payload.get("language", ""),
                "source": payload.get("source", ""),
                "score": score,
                "timestamp": payload.get("timestamp", ""),
                "_best_score": score,
                "_chunk_count": 1,
            }

    # Boost score by number of matching chunks (diminishing returns)
    results = []
    for doc in docs.values():
        chunk_count = doc.pop("_chunk_count")
        best_score = doc.pop("_best_score")
        # Score = best_chunk_score * (1 + log(chunk_count)/5)
        doc["score"] = round(best_score * (1 + math.log(chunk_count) / 5), 4) if best_score else 0
        doc["matching_chunks"] = chunk_count
        results.append(doc)

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def search(
    client: QdrantClient,
    bm25: BM25Encoder,
    query: str,
    mode: str = "hybrid",
    lang: str | None = None,
    source: str | None = None,
    limit: int = 10,
    offset: int = 0,
    group_by: str = "docs",
) -> dict:
    """Execute a search and return formatted results.

    Args:
        mode: "hybrid", "bm25", or "vector"
        group_by: "docs" (deduplicated, one per document) or "chunks" (all chunks)
    """
    start = time.time()
    query_filter = _build_filter(lang, source)

    # For doc mode, fetch extra to ensure enough unique docs after dedup
    if group_by == "docs":
        fetch_limit = (limit + offset) * 5
    else:
        fetch_limit = limit + offset

    if mode == "bm25":
        points = search_bm25(client, bm25, query, limit=fetch_limit, query_filter=query_filter)
    elif mode == "vector":
        points = search_vector(client, query, limit=fetch_limit, query_filter=query_filter)
    else:  # hybrid (default)
        points = search_hybrid(client, bm25, query, limit=fetch_limit, query_filter=query_filter)

    if group_by == "docs":
        results = _deduplicate_to_docs(points, query)
        results = results[offset:][:limit]
    else:
        # Chunk mode: return all chunks as-is
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
                "full_text_url": payload.get("full_text_url", ""),
                "content_type": payload.get("content_type", "full_text"),
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
        "group_by": group_by,
        "results": results,
        "total": len(results),
        "took_ms": took_ms,
    }
