"""Ingestion pipeline — fetches and stores documents from various sources."""

from ingestion.base import Document, SourceAdapter
from ingestion.storage import ContentStore

__all__ = ["Document", "SourceAdapter", "ContentStore"]
