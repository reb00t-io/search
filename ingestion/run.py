#!/usr/bin/env python3
"""Run the ingestion pipeline.

Usage:
    python -m ingestion.run [--limit N] [--data-dir DIR] [--sources wiki,arxiv]
"""

from __future__ import annotations

import argparse
import logging
import sys

from ingestion.arxiv_adapter import ArxivAdapter
from ingestion.storage import ContentStore
from ingestion.wikipedia import WikipediaAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ADAPTERS = {
    "wiki": WikipediaAdapter,
    "arxiv": ArxivAdapter,
}


def main():
    parser = argparse.ArgumentParser(description="Run ingestion pipeline")
    parser.add_argument("--limit", type=int, default=100, help="Max documents per source")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--sources", default="wiki,arxiv", help="Comma-separated source names")
    args = parser.parse_args()

    store = ContentStore(args.data_dir)
    source_names = [s.strip() for s in args.sources.split(",")]

    total = 0
    for name in source_names:
        adapter_cls = ADAPTERS.get(name)
        if not adapter_cls:
            logger.error("Unknown source: %s (available: %s)", name, ", ".join(ADAPTERS))
            continue

        adapter = adapter_cls()
        count = 0
        logger.info("Starting ingestion from %s (limit=%d)", name, args.limit)

        for doc in adapter.bulk_ingest(limit=args.limit):
            store.store(doc)
            count += 1
            if count % 10 == 0:
                logger.info("  %s: %d documents ingested", name, count)
            if count >= args.limit:
                break

        logger.info("Finished %s: %d documents", name, count)
        total += count

    logger.info("Total ingested: %d documents", total)


if __name__ == "__main__":
    main()
