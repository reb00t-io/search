#!/usr/bin/env python3
"""Run the filtering pipeline.

Reads ingested documents, applies quality + safety filters, writes filtered output.

Usage:
    python -m filtering.run [--data-dir DIR] [--min-length N]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from filtering.filters import filter_document
from ingestion.storage import ContentStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run filtering pipeline")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--min-length", type=int, default=200, help="Minimum text length")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    store = ContentStore(args.data_dir)

    # Read ingested records
    records = store.load_records()
    logger.info("Loaded %d ingested records", len(records))

    filtered_dir = data_dir / "filtered"
    rejected_dir = data_dir / "rejected"
    filtered_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    filtered_path = filtered_dir / "documents.jsonl"
    rejected_path = rejected_dir / "rejected.jsonl"

    accepted = 0
    rejected = 0

    with open(filtered_path, "w", encoding="utf-8") as f_out, \
         open(rejected_path, "w", encoding="utf-8") as f_rej:

        for record in records:
            content_hash = record["content_hash"]
            try:
                text = store.read_content(content_hash)
            except FileNotFoundError:
                logger.warning("Content missing for %s (hash %s)", record["id"], content_hash)
                continue

            result = filter_document(text, min_length=args.min_length)

            if result.accepted:
                f_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                accepted += 1
            else:
                record_with_reason = {**record, "rejection_reason": result.reason}
                f_rej.write(json.dumps(record_with_reason, ensure_ascii=False) + "\n")
                rejected += 1

    logger.info("Filtering complete: %d accepted, %d rejected", accepted, rejected)


if __name__ == "__main__":
    main()
