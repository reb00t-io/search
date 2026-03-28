#!/usr/bin/env python3
"""Run the ingestion pipeline incrementally.

Tracks which documents have been ingested. On restart, skips already-ingested
items and picks up where it left off.

Usage:
    python -m ingestion.run [--limit N] [--data-dir DIR] [--sources wiki,arxiv]
"""

from __future__ import annotations

import argparse
import logging

from ingestion.arxiv_adapter import ArxivAdapter
from ingestion.cursor import IdCursor
from ingestion.gesetze import GesetzeAdapter
from ingestion.pubmed import PubmedAdapter
from ingestion.rki import RkiAdapter
from ingestion.rss_adapter import DWAdapter, TagesschauAdapter
from ingestion.storage import ContentStore
from ingestion.wikipedia import WikipediaAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ADAPTERS = {
    "wiki": WikipediaAdapter,
    "arxiv": ArxivAdapter,
    "gesetze": GesetzeAdapter,
    "pubmed": PubmedAdapter,
    "rki": RkiAdapter,
    "tagesschau": TagesschauAdapter,
    "dw": DWAdapter,
}


def main():
    parser = argparse.ArgumentParser(description="Run ingestion pipeline")
    parser.add_argument("--limit", type=int, default=100, help="Max NEW documents per source per run")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--sources", default="wiki,arxiv,gesetze,pubmed,rki,tagesschau,dw", help="Comma-separated source names")
    args = parser.parse_args()

    store = ContentStore(args.data_dir)
    source_names = [s.strip() for s in args.sources.split(",")]

    total_new = 0
    for name in source_names:
        adapter_cls = ADAPTERS.get(name)
        if not adapter_cls:
            logger.error("Unknown source: %s (available: %s)", name, ", ".join(ADAPTERS))
            continue

        cursor = IdCursor(store.data_dir / "cursors" / f"{name}.json")
        adapter = adapter_cls()
        new_count = 0
        skipped = 0
        logger.info("Starting ingestion from %s (limit=%d, already have %d)", name, args.limit, len(cursor.seen_ids))

        for doc in adapter.bulk_ingest(limit=args.limit + len(cursor.seen_ids)):
            if cursor.has(doc.id):
                skipped += 1
                continue

            store.store(doc)
            cursor.add(doc.id)
            new_count += 1

            if new_count % 10 == 0:
                logger.info("  %s: %d new documents ingested (%d skipped)", name, new_count, skipped)
                cursor.save()

            if new_count >= args.limit:
                break

        cursor.save()
        logger.info("Finished %s: %d new, %d skipped, %d total tracked", name, new_count, skipped, len(cursor.seen_ids))
        total_new += new_count

    logger.info("Total new documents ingested: %d", total_new)


if __name__ == "__main__":
    main()
