"""Tests for pipeline maintenance (source purge for re-ingestion)."""

import json
from pathlib import Path

from ingestion.maintenance import (
    purge_source,
    read_cursor_offset,
    rewrite_jsonl_without_source,
    write_cursor_offset,
)


def _write_jsonl(path: Path, records: list[dict]) -> list[int]:
    """Write records; return cumulative byte offsets after each line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    offsets = []
    pos = 0
    with open(path, "wb") as f:
        for rec in records:
            raw = (json.dumps(rec) + "\n").encode()
            f.write(raw)
            pos += len(raw)
            offsets.append(pos)
    return offsets


def test_rewrite_drops_source_and_remaps_offset(tmp_path):
    path = tmp_path / "documents.jsonl"
    records = [
        {"id": "wiki:1", "source": "wiki"},
        {"id": "gesetze:estg:0", "source": "gesetze"},
        {"id": "bmf:1", "source": "bmf"},
        {"id": "gesetze:estg:1", "source": "gesetze"},
    ]
    offsets = _write_jsonl(path, records)
    # Cursor consumed the first three lines
    removed, new_offset = rewrite_jsonl_without_source(path, "gesetze", offsets[2])

    assert removed == 2
    remaining = [json.loads(l) for l in path.read_text().splitlines()]
    assert [r["id"] for r in remaining] == ["wiki:1", "bmf:1"]
    # New offset covers exactly the surviving consumed lines (wiki:1 + bmf:1)
    consumed = path.read_bytes()[:new_offset].decode()
    assert "wiki:1" in consumed and "bmf:1" in consumed
    assert consumed.endswith("\n")


def test_rewrite_keeps_unparseable_lines(tmp_path):
    path = tmp_path / "documents.jsonl"
    path.write_text('{"id": "wiki:1", "source": "wiki"}\nnot json\n')
    removed, _ = rewrite_jsonl_without_source(path, "gesetze", 0)
    assert removed == 0
    assert "not json" in path.read_text()


def test_rewrite_missing_file(tmp_path):
    assert rewrite_jsonl_without_source(tmp_path / "nope.jsonl", "gesetze", 0) == (0, 0)


def test_purge_source_full_flow(tmp_path):
    data_dir = tmp_path
    ingested = [
        {"id": "wiki:1", "source": "wiki"},
        {"id": "gesetze:estg:0", "source": "gesetze"},
    ]
    filtered = list(ingested)
    ingested_offsets = _write_jsonl(data_dir / "ingested" / "documents.jsonl", ingested)
    filtered_offsets = _write_jsonl(data_dir / "filtered" / "documents.jsonl", filtered)
    _write_jsonl(data_dir / "rejected" / "rejected.jsonl", [{"id": "gesetze:x:0", "source": "gesetze"}])

    write_cursor_offset(data_dir / "cursors" / "filtering.json", ingested_offsets[-1])
    write_cursor_offset(data_dir / "cursors" / "indexing.json", filtered_offsets[-1])
    (data_dir / "cursors" / "gesetze.json").write_text('{"ids": ["gesetze:estg:0"]}')

    stats = purge_source(data_dir, "gesetze")

    assert stats["ingested/documents.jsonl"]["removed"] == 1
    assert stats["filtered/documents.jsonl"]["removed"] == 1
    assert stats["rejected/rejected.jsonl"]["removed"] == 1
    assert stats["ingestion_cursor_deleted"] is True
    assert not (data_dir / "cursors" / "gesetze.json").exists()

    # Cursors now point at the end of the rewritten files (fully consumed)
    for name in ("ingested", "filtered"):
        file_size = (data_dir / name / "documents.jsonl").stat().st_size
        cursor = read_cursor_offset(data_dir / "cursors" / ("filtering.json" if name == "ingested" else "indexing.json"))
        assert cursor == file_size
