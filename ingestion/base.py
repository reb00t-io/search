"""Base classes for ingestion pipeline."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

# Content type constants
CONTENT_ABSTRACT = "abstract"
CONTENT_FULL_TEXT = "full_text"


@dataclasses.dataclass
class Document:
    """Common document format emitted by all source adapters."""

    id: str  # e.g. "wiki:de:12345:0"
    source: str  # e.g. "wiki", "arxiv"
    title: str
    url: str
    language: str  # "de" or "en"
    text: str  # markdown content
    content_type: str = CONTENT_FULL_TEXT  # "abstract" or "full_text"
    full_text_url: str = ""  # URL to full text (empty if already full text or unavailable)
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    timestamp: str = ""  # ISO 8601


class SourceAdapter:
    """Base class for all source adapters."""

    name: str = ""

    def bulk_ingest(self, limit: int | None = None, known_ids: set[str] | None = None) -> Iterator[Document]:
        """Initial/periodic full ingestion. Yields Documents.

        Args:
            known_ids: Set of already-ingested document IDs. Adapters may use
                       this to skip expensive fetches for content that would be
                       deduplicated anyway.
        """
        raise NotImplementedError

    def stream_updates(self) -> Iterator[Document]:
        """Continuous stream of new/changed documents. May be a no-op."""
        return iter([])

    def document_url(self, doc_id: str) -> str:
        """Canonical URL for a document ID."""
        raise NotImplementedError
