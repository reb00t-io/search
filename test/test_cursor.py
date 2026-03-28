"""Tests for incremental cursor utilities."""

import json
import tempfile
from pathlib import Path

from ingestion.cursor import IdCursor, JsonlCursor


class TestJsonlCursor:
    def test_initial_offset_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = JsonlCursor(Path(tmp) / "cursor.json")
            assert cursor.offset == 0

    def test_reads_all_lines_on_first_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "data.jsonl"
            jsonl.write_text('{"id": "a"}\n{"id": "b"}\n')

            cursor = JsonlCursor(Path(tmp) / "cursor.json")
            records = cursor.read_new_lines(jsonl)
            assert len(records) == 2
            assert records[0]["id"] == "a"
            assert records[1]["id"] == "b"

    def test_skips_already_read_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "data.jsonl"
            jsonl.write_text('{"id": "a"}\n{"id": "b"}\n')

            cursor = JsonlCursor(Path(tmp) / "cursor.json")
            records = cursor.read_new_lines(jsonl)
            assert len(records) == 2
            cursor.save()

            # No new data
            records = cursor.read_new_lines(jsonl)
            assert len(records) == 0

    def test_reads_only_new_lines_after_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "data.jsonl"
            jsonl.write_text('{"id": "a"}\n')

            cursor = JsonlCursor(Path(tmp) / "cursor.json")
            records = cursor.read_new_lines(jsonl)
            assert len(records) == 1
            cursor.save()

            # Append new data
            with open(jsonl, "a") as f:
                f.write('{"id": "b"}\n{"id": "c"}\n')

            records = cursor.read_new_lines(jsonl)
            assert len(records) == 2
            assert records[0]["id"] == "b"
            assert records[1]["id"] == "c"

    def test_persists_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "data.jsonl"
            jsonl.write_text('{"id": "a"}\n{"id": "b"}\n')
            cursor_path = Path(tmp) / "cursor.json"

            cursor = JsonlCursor(cursor_path)
            cursor.read_new_lines(jsonl)
            cursor.save()

            # "Restart" — new cursor instance
            cursor2 = JsonlCursor(cursor_path)
            records = cursor2.read_new_lines(jsonl)
            assert len(records) == 0

            # Append new data
            with open(jsonl, "a") as f:
                f.write('{"id": "c"}\n')
            records = cursor2.read_new_lines(jsonl)
            assert len(records) == 1
            assert records[0]["id"] == "c"

    def test_handles_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = JsonlCursor(Path(tmp) / "cursor.json")
            records = cursor.read_new_lines(Path(tmp) / "nonexistent.jsonl")
            assert len(records) == 0


class TestIdCursor:
    def test_initial_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = IdCursor(Path(tmp) / "cursor.json")
            assert len(cursor.seen_ids) == 0

    def test_add_and_has(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = IdCursor(Path(tmp) / "cursor.json")
            assert not cursor.has("doc:1")
            cursor.add("doc:1")
            assert cursor.has("doc:1")

    def test_persists_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor_path = Path(tmp) / "cursor.json"
            cursor = IdCursor(cursor_path)
            cursor.add("doc:1")
            cursor.add("doc:2")
            cursor.save()

            cursor2 = IdCursor(cursor_path)
            assert cursor2.has("doc:1")
            assert cursor2.has("doc:2")
            assert not cursor2.has("doc:3")

    def test_no_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cursor = IdCursor(Path(tmp) / "cursor.json")
            cursor.add("doc:1")
            cursor.add("doc:1")
            assert len(cursor.seen_ids) == 1
