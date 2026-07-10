"""Full-document lookup by ID.

Search and RAG return chunk text truncated to the payload snippet limit; this
module resolves a document ID back to the complete markdown from the content
store, so clients (e.g. the tax-agent) can quote sources verbatim.

IDs are chunk IDs ("gesetze:estg:3"), §-style IDs ("gesetze:hgb:267a",
resolved via metadata.sections) or base document IDs
("gesetze:estg" — all chunks concatenated in order). Lookup data comes from
data/filtered/documents.jsonl (the indexed corpus) and is cached in memory;
the file is reloaded when its size changes (it is append-only, written by the
nightly filter run).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ingestion.storage import ContentStore
from serving.search import _base_doc_id

logger = logging.getLogger(__name__)


def _chunk_index(doc_id: str) -> int:
    """Trailing chunk index of an ID, or 0 if it has none."""
    tail = doc_id.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 0


class DocumentLookup:
    """In-memory ID -> metadata-record index over filtered/documents.jsonl."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.jsonl_path = self.data_dir / "filtered" / "documents.jsonl"
        self.store = ContentStore(self.data_dir)
        self._records: dict[str, dict] = {}
        self._chunks_by_base: dict[str, list[str]] = {}
        self._loaded_size = -1

    def _refresh(self) -> None:
        try:
            size = self.jsonl_path.stat().st_size
        except FileNotFoundError:
            self._records, self._chunks_by_base, self._loaded_size = {}, {}, -1
            return
        if size == self._loaded_size:
            return

        records: dict[str, dict] = {}
        chunks_by_base: dict[str, list[str]] = {}
        for line in self.jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            doc_id = record.get("id", "")
            if not doc_id:
                continue
            # Later records win (re-ingested documents supersede old ones)
            if doc_id not in records:
                chunks_by_base.setdefault(_base_doc_id(doc_id), []).append(doc_id)
            records[doc_id] = record

        for chunk_ids in chunks_by_base.values():
            chunk_ids.sort(key=_chunk_index)

        self._records = records
        self._chunks_by_base = chunks_by_base
        self._loaded_size = size
        logger.info("Document lookup loaded: %d records, %d documents",
                    len(records), len(chunks_by_base))

    def get_records(self, doc_id: str) -> list[dict]:
        """Records for an exact chunk ID, all chunks of a base document ID, or
        a §-style ID ("gesetze:hgb:267a" — the chunk containing § 267a, via the
        chunks' metadata.sections from §-aligned ingestion).

        Returns an empty list if the ID is unknown.
        """
        self._refresh()
        if doc_id in self._records:
            return [self._records[doc_id]]
        chunk_ids = self._chunks_by_base.get(doc_id, [])
        if chunk_ids:
            return [self._records[cid] for cid in chunk_ids]
        return self._section_records(doc_id)

    def _section_records(self, doc_id: str) -> list[dict]:
        """Resolve "gesetze:<slug>:<§-number>" via metadata.sections.

        LLM clients naturally cite laws this way; numeric tails stay chunk
        indexes, so only non-numeric tails (e.g. "267a", "8b") resolve here.
        """
        base, _, tail = doc_id.rpartition(":")
        if not base or not tail or tail.isdigit():
            return []
        # "267a" and "§23"/"§ 23" both resolve; a bare numeric tail ("23")
        # stays a chunk index for backwards compatibility.
        wanted = tail.lower().lstrip("§").strip()
        for chunk_id in self._chunks_by_base.get(base, []):
            record = self._records[chunk_id]
            sections = (record.get("metadata") or {}).get("sections") or []
            for section in sections:
                if section.lower().lstrip("§").strip() == wanted:
                    return [record]
        return []

    def read_text(self, records: list[dict]) -> str:
        """Concatenate the content of the given records, skipping missing files."""
        texts = []
        for record in records:
            try:
                texts.append(self.store.read_content(record["content_hash"]))
            except (FileNotFoundError, KeyError):
                logger.warning("Missing content for %s", record.get("id"))
        return "\n\n".join(texts)


def fetch_document(lookup: DocumentLookup, doc_id: str, max_chars: int) -> dict | None:
    """Resolve a document ID to its full text. None if unknown."""
    records = lookup.get_records(doc_id)
    if not records:
        return None

    text = lookup.read_text(records)
    truncated = len(text) > max_chars
    first = records[0]
    return {
        "id": doc_id,
        "title": first.get("title", ""),
        "url": first.get("url", ""),
        "source": first.get("source", ""),
        "language": first.get("language", ""),
        "timestamp": first.get("timestamp", ""),
        "chunks": len(records),
        "text": text[:max_chars],
        "truncated": truncated,
    }
