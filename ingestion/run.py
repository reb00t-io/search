#!/usr/bin/env python3
"""Run the ingestion pipeline incrementally with round-robin across sources.

Pulls a few documents from each source in turn to spread load evenly
and avoid hammering any single site.

Usage:
    python -m ingestion.run [--limit N] [--data-dir DIR] [--sources wiki,arxiv,...] [--batch-size N]
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

from ingestion.arxiv_adapter import ArxivAdapter
from ingestion.base import Document
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


class SourceSlot:
    """Tracks one source's iterator, cursor, and counts during round-robin ingestion."""

    def __init__(self, name: str, iterator: Iterator[Document], cursor: IdCursor, limit: int):
        self.name = name
        self.iterator = iterator
        self.cursor = cursor
        self.limit = limit
        self.new_count = 0
        self.skipped = 0
        self.exhausted = False

    @property
    def done(self) -> bool:
        return self.exhausted or self.new_count >= self.limit

    def pull_batch(self, store: ContentStore, batch_size: int) -> int:
        """Pull up to batch_size new documents. Returns count of new docs stored."""
        stored = 0
        while stored < batch_size and not self.done:
            try:
                doc = next(self.iterator)
            except StopIteration:
                self.exhausted = True
                break

            if self.cursor.has(doc.id):
                self.skipped += 1
                continue

            store.store(doc)
            self.cursor.add(doc.id)
            self.new_count += 1
            stored += 1

        return stored


INGESTION_TZ = ZoneInfo("Europe/Berlin")
INGESTION_START_HOUR = 0
INGESTION_END_HOUR = 6

_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    logger.info("Received signal %d, shutting down after current batch", signum)
    _shutdown = True


def _in_ingestion_window() -> bool:
    now = datetime.now(INGESTION_TZ)
    return INGESTION_START_HOUR <= now.hour < INGESTION_END_HOUR


def _seconds_until_window() -> float:
    from datetime import timedelta
    now = datetime.now(INGESTION_TZ)
    target = now.replace(hour=INGESTION_START_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _run_one_cycle(args) -> int:
    """Run one ingestion cycle. Returns total new documents ingested."""
    store = ContentStore(args.data_dir)
    source_names = [s.strip() for s in args.sources.split(",")]

    slots: list[SourceSlot] = []
    for name in source_names:
        adapter_cls = ADAPTERS.get(name)
        if not adapter_cls:
            logger.error("Unknown source: %s (available: %s)", name, ", ".join(ADAPTERS))
            continue

        cursor = IdCursor(store.data_dir / "cursors" / f"{name}.json")
        adapter = adapter_cls()
        iterator = adapter.bulk_ingest(limit=args.limit + len(cursor.seen_ids), known_ids=cursor.seen_ids)
        slots.append(SourceSlot(name, iterator, cursor, args.limit))
        logger.info("Initialized %s (already have %d)", name, len(cursor.seen_ids))

    round_num = 0
    while not _shutdown:
        active = [s for s in slots if not s.done]
        if not active:
            break

        if not _in_ingestion_window():
            logger.info("Outside ingestion window, stopping cycle")
            break

        round_num += 1
        round_total = 0
        for slot in active:
            stored = slot.pull_batch(store, args.batch_size)
            round_total += stored

        if round_total == 0:
            break

        if round_num % 10 == 0:
            status = ", ".join(f"{s.name}:{s.new_count}" for s in slots)
            logger.info("Round %d: %s", round_num, status)

    total_new = 0
    for slot in slots:
        slot.cursor.save()
        logger.info("Finished %s: %d new, %d skipped, %d total tracked",
                    slot.name, slot.new_count, slot.skipped, len(slot.cursor.seen_ids))
        total_new += slot.new_count

    return total_new


def main():
    parser = argparse.ArgumentParser(description="Run ingestion pipeline")
    parser.add_argument("--limit", type=int, default=100, help="Max new documents per source per cycle")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--sources", default="wiki,arxiv,gesetze,pubmed,rki,tagesschau,dw", help="Comma-separated source names")
    parser.add_argument("--batch-size", type=int, default=3, help="Documents to pull per source per round")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit (ignore time window)")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        total = _run_one_cycle(args)
        logger.info("Total new documents ingested: %d", total)
        return

    # Continuous mode: ingest during window, sleep outside it
    while not _shutdown:
        if _in_ingestion_window():
            logger.info("Ingestion window open (%02d:00–%02d:00 %s), starting cycle",
                        INGESTION_START_HOUR, INGESTION_END_HOUR, INGESTION_TZ)
            total = _run_one_cycle(args)
            logger.info("Cycle complete: %d new documents ingested", total)
            if not _shutdown and _in_ingestion_window():
                time.sleep(30)  # brief pause between cycles
        else:
            wait = _seconds_until_window()
            logger.info("Outside ingestion window, sleeping %.0f minutes until %02d:00 %s",
                        wait / 60, INGESTION_START_HOUR, INGESTION_TZ)
            # Sleep in short intervals so we can respond to signals
            while wait > 0 and not _shutdown:
                time.sleep(min(wait, 60))
                wait = _seconds_until_window()


if __name__ == "__main__":
    main()
