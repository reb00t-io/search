#!/usr/bin/env python3
"""Run the filtering pipeline continuously.

Watches ingested/documents.jsonl for new records, filters them, and appends
results to filtered/documents.jsonl. Waits for new data when caught up.

Usage:
    python -m filtering.run [--data-dir DIR] [--min-length N] [--poll-interval SEC] [--once]
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from pathlib import Path

from filtering.filters import filter_document
from ingestion.cursor import JsonlCursor
from ingestion.storage import ContentStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Shutdown signal received, finishing current batch...")
    _shutdown = True


def process_batch(
    records: list[dict],
    store: ContentStore,
    filtered_path: Path,
    rejected_path: Path,
    min_length: int,
) -> tuple[int, int]:
    """Filter a batch of records. Returns (accepted, rejected) counts."""
    accepted = 0
    rejected = 0

    with open(filtered_path, "a", encoding="utf-8") as f_out, \
         open(rejected_path, "a", encoding="utf-8") as f_rej:

        for record in records:
            content_hash = record["content_hash"]
            try:
                text = store.read_content(content_hash)
            except FileNotFoundError:
                logger.warning("Content missing for %s (hash %s)", record["id"], content_hash)
                continue

            result = filter_document(text, min_length=min_length)

            if result.accepted:
                f_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                accepted += 1
            else:
                record_with_reason = {**record, "rejection_reason": result.reason}
                f_rej.write(json.dumps(record_with_reason, ensure_ascii=False) + "\n")
                rejected += 1

    return accepted, rejected


def main():
    parser = argparse.ArgumentParser(description="Run filtering pipeline")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--min-length", type=int, default=200, help="Minimum text length")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between polls for new data")
    parser.add_argument("--once", action="store_true", help="Process available data and exit (don't wait)")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    data_dir = Path(args.data_dir)
    store = ContentStore(args.data_dir)
    ingested_path = data_dir / "ingested" / "documents.jsonl"

    filtered_dir = data_dir / "filtered"
    rejected_dir = data_dir / "rejected"
    filtered_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    filtered_path = filtered_dir / "documents.jsonl"
    rejected_path = rejected_dir / "rejected.jsonl"

    cursor = JsonlCursor(data_dir / "cursors" / "filtering.json")
    logger.info("Filtering starting (offset=%d, poll=%.1fs, once=%s)", cursor.offset, args.poll_interval, args.once)

    total_accepted = 0
    total_rejected = 0

    while not _shutdown:
        new_records = cursor.read_new_lines(ingested_path)

        if new_records:
            accepted, rejected = process_batch(new_records, store, filtered_path, rejected_path, args.min_length)
            cursor.save()
            total_accepted += accepted
            total_rejected += rejected
            logger.info("Filtered %d records: %d accepted, %d rejected (total: %d/%d)",
                        len(new_records), accepted, rejected, total_accepted, total_rejected)
        else:
            if args.once:
                break
            time.sleep(args.poll_interval)

    logger.info("Filtering stopped. Total: %d accepted, %d rejected", total_accepted, total_rejected)


if __name__ == "__main__":
    main()
