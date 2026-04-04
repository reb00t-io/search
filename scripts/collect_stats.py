#!/usr/bin/env python3
"""Collect index statistics and append a daily snapshot to history.

Designed to run once per day (e.g. 06:30 after nightly processing).
Appends one JSON line to data/stats/history.jsonl.

Usage:
    python scripts/collect_stats.py [--data-dir DIR] [--qdrant-url URL]
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def collect(data_dir: Path, qdrant_url: str) -> dict:
    from qdrant_client import QdrantClient
    from indexing.indexer import COLLECTION_NAME

    stats: dict = {"timestamp": datetime.now(timezone.utc).isoformat()}

    # Qdrant info
    client = QdrantClient(url=qdrant_url, timeout=10)
    try:
        info = client.get_collection(COLLECTION_NAME)
        stats["indexed_points"] = info.points_count or 0
        stats["segments"] = info.segments_count or 0
    except Exception:
        stats["indexed_points"] = 0
        stats["segments"] = 0

    # Document counts from filtered JSONL
    filtered_path = data_dir / "filtered" / "documents.jsonl"
    source_counts: Counter = Counter()
    ct_counts: Counter = Counter()
    if filtered_path.exists():
        for line in filtered_path.read_text().strip().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                source_counts[rec.get("source", "?")] += 1
                ct_counts[rec.get("content_type", "full_text")] += 1
            except json.JSONDecodeError:
                pass

    stats["documents"] = sum(source_counts.values())
    stats["full_text"] = ct_counts.get("full_text", 0)
    stats["abstracts"] = ct_counts.get("abstract", 0)
    stats["by_source"] = dict(sorted(source_counts.items()))
    stats["content_bytes"] = _dir_size(data_dir / "content")
    stats["data_bytes"] = _dir_size(data_dir)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Collect daily index stats")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--qdrant-url", default="http://localhost:6333", help="Qdrant URL")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    stats = collect(data_dir, args.qdrant_url)

    history_dir = data_dir / "stats"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / "history.jsonl"

    with open(history_path, "a") as f:
        f.write(json.dumps(stats) + "\n")

    logger.info(
        "Stats collected: %d docs, %d indexed, %s content",
        stats["documents"],
        stats["indexed_points"],
        f"{stats['content_bytes'] / (1 << 20):.1f} MB",
    )


if __name__ == "__main__":
    main()
