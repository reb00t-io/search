"""Maintenance operations on the pipeline data files.

Currently: purging one source from the pipeline so it can be re-ingested from
scratch (e.g. after a chunking change). The JSONL files are append-only and
consumed via byte-offset cursors, so purging must rewrite the files AND remap
the affected cursor offsets — otherwise filtering/indexing would re-process or
skip records.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def rewrite_jsonl_without_source(path: Path, source: str, old_offset: int) -> tuple[int, int]:
    """Rewrite a JSONL file dropping all records of `source`.

    Returns (removed_count, new_offset) where new_offset is the byte position
    in the rewritten file equivalent to old_offset in the original (i.e. the
    consumed prefix minus the removed lines). Unparseable lines are kept.
    """
    if not path.exists():
        return 0, 0

    tmp = path.with_name(path.name + ".rewrite-tmp")
    removed = 0
    new_offset = 0
    pos = 0
    with open(path, "rb") as f_in, open(tmp, "wb") as f_out:
        for raw in f_in:
            pos += len(raw)
            keep = True
            try:
                if json.loads(raw).get("source") == source:
                    keep = False
            except (json.JSONDecodeError, AttributeError):
                keep = True
            if keep:
                f_out.write(raw)
                if pos <= old_offset:
                    new_offset += len(raw)
            else:
                removed += 1
    tmp.replace(path)
    return removed, new_offset


def read_cursor_offset(cursor_path: Path) -> int:
    if not cursor_path.exists():
        return 0
    try:
        return json.loads(cursor_path.read_text(encoding="utf-8")).get("offset", 0)
    except (json.JSONDecodeError, KeyError):
        return 0


def write_cursor_offset(cursor_path: Path, offset: int) -> None:
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(json.dumps({"offset": offset}), encoding="utf-8")


def purge_source(data_dir: str | Path, source: str) -> dict:
    """Remove one source from ingested/filtered JSONL files and reset its
    ingestion cursor, keeping the filtering/indexing byte-offset cursors
    consistent. Content files (content-addressed) are left in place.

    Must run while ingestion/filtering/indexing services are stopped.
    """
    data_dir = Path(data_dir)
    stats: dict = {"source": source}

    for jsonl_name, cursor_name in (
        ("ingested/documents.jsonl", "cursors/filtering.json"),
        ("filtered/documents.jsonl", "cursors/indexing.json"),
    ):
        jsonl_path = data_dir / jsonl_name
        cursor_path = data_dir / cursor_name
        old_offset = read_cursor_offset(cursor_path)
        removed, new_offset = rewrite_jsonl_without_source(jsonl_path, source, old_offset)
        if cursor_path.exists():
            write_cursor_offset(cursor_path, new_offset)
        stats[jsonl_name] = {"removed": removed, "old_offset": old_offset, "new_offset": new_offset}
        logger.info("%s: removed %d %s records (cursor %d -> %d)",
                    jsonl_name, removed, source, old_offset, new_offset)

    # Rejected records of the source would resurface confusion later; drop too.
    rejected_path = data_dir / "rejected" / "rejected.jsonl"
    removed, _ = rewrite_jsonl_without_source(rejected_path, source, 0)
    stats["rejected/rejected.jsonl"] = {"removed": removed}

    ingest_cursor = data_dir / "cursors" / f"{source}.json"
    if ingest_cursor.exists():
        ingest_cursor.unlink()
        logger.info("Deleted ingestion cursor %s", ingest_cursor)
        stats["ingestion_cursor_deleted"] = True

    return stats


def delete_source_points(qdrant_url: str, source: str, collection: str = "documents") -> None:
    """Delete all Qdrant points of one source."""
    from qdrant_client import QdrantClient, models

    client = QdrantClient(url=qdrant_url)
    client.delete(
        collection_name=collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(must=[
                models.FieldCondition(key="source", match=models.MatchValue(value=source)),
            ])
        ),
        wait=True,
    )
    logger.info("Deleted %s points from Qdrant collection %s", source, collection)
