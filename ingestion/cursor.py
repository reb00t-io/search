"""Cursor utilities for incremental JSONL processing."""

from __future__ import annotations

import json
from pathlib import Path


class JsonlCursor:
    """Tracks byte offset into a JSONL file for incremental reading.

    Saves offset to a cursor file so processing resumes after restart.
    """

    def __init__(self, cursor_path: str | Path):
        self.cursor_path = Path(cursor_path)
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        self._offset = self._load_offset()

    @property
    def offset(self) -> int:
        return self._offset

    def _load_offset(self) -> int:
        if self.cursor_path.exists():
            try:
                data = json.loads(self.cursor_path.read_text(encoding="utf-8"))
                return data.get("offset", 0)
            except (json.JSONDecodeError, KeyError):
                return 0
        return 0

    def save(self):
        self.cursor_path.write_text(
            json.dumps({"offset": self._offset}), encoding="utf-8"
        )

    def read_new_lines(self, jsonl_path: str | Path) -> list[dict]:
        """Read lines added since the last saved offset.

        Returns parsed records and advances the offset.
        Does NOT save — caller must call save() after successful processing.
        """
        path = Path(jsonl_path)
        if not path.exists():
            return []

        file_size = path.stat().st_size
        if file_size <= self._offset:
            return []

        records = []
        with open(path, "r", encoding="utf-8") as f:
            f.seek(self._offset)
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            self._offset = f.tell()

        return records


class IdCursor:
    """Tracks a set of already-processed IDs for deduplication.

    Used by ingestion to skip already-ingested documents.
    """

    def __init__(self, cursor_path: str | Path):
        self.cursor_path = Path(cursor_path)
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        self.seen_ids: set[str] = self._load()

    def _load(self) -> set[str]:
        if self.cursor_path.exists():
            try:
                data = json.loads(self.cursor_path.read_text(encoding="utf-8"))
                return set(data.get("ids", []))
            except (json.JSONDecodeError, KeyError):
                return set()
        return set()

    def save(self):
        self.cursor_path.write_text(
            json.dumps({"ids": sorted(self.seen_ids)}), encoding="utf-8"
        )

    def has(self, doc_id: str) -> bool:
        return doc_id in self.seen_ids

    def add(self, doc_id: str):
        self.seen_ids.add(doc_id)
