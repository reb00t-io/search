"""Content-addressed storage for ingested documents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ingestion.base import Document


class ContentStore:
    """Stores documents with content-hash deduplication.

    Markdown content is stored in separate files keyed by SHA-256 hash.
    Metadata is appended to a JSONL index.
    """

    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)
        self.content_dir = self.data_dir / "content"
        self.ingested_dir = self.data_dir / "ingested"
        self.content_dir.mkdir(parents=True, exist_ok=True)
        self.ingested_dir.mkdir(parents=True, exist_ok=True)

    def _content_path(self, content_hash: str) -> Path:
        """Shard by first two byte-pairs: aa/bb/aabb...md"""
        return self.content_dir / content_hash[:2] / content_hash[2:4] / f"{content_hash}.md"

    def _write_content(self, text: str) -> str:
        """Write content file, return its SHA-256 hash."""
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        path = self._content_path(content_hash)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return content_hash

    def read_content(self, content_hash: str) -> str:
        """Read content by hash."""
        return self._content_path(content_hash).read_text(encoding="utf-8")

    def store(self, doc: Document) -> str:
        """Store a document. Returns the content hash."""
        content_hash = self._write_content(doc.text)
        record = {
            "id": doc.id,
            "source": doc.source,
            "title": doc.title,
            "url": doc.url,
            "language": doc.language,
            "content_hash": content_hash,
            "metadata": doc.metadata,
            "timestamp": doc.timestamp,
        }
        jsonl_path = self.ingested_dir / "documents.jsonl"
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return content_hash

    def load_records(self, jsonl_path: str | Path | None = None) -> list[dict]:
        """Load all metadata records from JSONL."""
        if jsonl_path is None:
            jsonl_path = self.ingested_dir / "documents.jsonl"
        path = Path(jsonl_path)
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records
