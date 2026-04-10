#!/usr/bin/env python3
"""Run the ingestion pipeline incrementally with round-robin across sources.

Pulls a few documents from each source in turn to spread load evenly
and avoid hammering any single site.

The schedule and per-source limit are configurable at startup via CLI args
or environment variables. Production runs nightly between 00:00 and 06:00
Europe/Berlin; for testing, set --start-hour/--start-minute/--duration to a
near-future window so you can observe ingestion happen.

Usage:
    python -m ingestion.run [--limit N] [--data-dir DIR] [--sources wiki,arxiv,...]
    python -m ingestion.run --start-hour 14 --start-minute 30 --duration 30 --limit 5
    python -m ingestion.run --once       # Run one cycle ignoring the schedule
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
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


DEFAULT_TZ = ZoneInfo("Europe/Berlin")
DEFAULT_START_HOUR = 0
DEFAULT_START_MINUTE = 0
DEFAULT_DURATION_MINUTES = 360  # 00:00–06:00
DEFAULT_LIMIT_PER_SOURCE = 100
SCHEDULE_FILE_NAME = "ingestion_schedule.json"

_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    logger.info("Received signal %d, shutting down after current batch", signum)
    _shutdown = True


@dataclass
class Schedule:
    """Daily ingestion window: opens at start_hour:start_minute, lasts duration_minutes.

    Handles windows that cross midnight by also checking yesterday's window
    against the current time.
    """

    start_hour: int = DEFAULT_START_HOUR
    start_minute: int = DEFAULT_START_MINUTE
    duration_minutes: int = DEFAULT_DURATION_MINUTES
    tz: ZoneInfo = DEFAULT_TZ

    def _start_on(self, day: datetime) -> datetime:
        return day.replace(
            hour=self.start_hour, minute=self.start_minute, second=0, microsecond=0
        )

    def in_window(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(self.tz)
        for delta_days in (0, -1):
            start = self._start_on(now) + timedelta(days=delta_days)
            end = start + timedelta(minutes=self.duration_minutes)
            if start <= now < end:
                return True
        return False

    def seconds_until_window(self, now: datetime | None = None) -> float:
        now = now or datetime.now(self.tz)
        if self.in_window(now):
            return 0.0
        start = self._start_on(now)
        if start <= now:
            start += timedelta(days=1)
        return (start - now).total_seconds()

    def end_hour_minute(self) -> tuple[int, int]:
        total_min = self.start_hour * 60 + self.start_minute + self.duration_minutes
        return (total_min // 60) % 24, total_min % 60

    def describe(self) -> str:
        end_hour, end_minute = self.end_hour_minute()
        return (
            f"{self.start_hour:02d}:{self.start_minute:02d}–"
            f"{end_hour:02d}:{end_minute:02d} {self.tz.key} "
            f"({self.duration_minutes} min)"
        )

    def to_dict(self) -> dict:
        end_hour, end_minute = self.end_hour_minute()
        return {
            "start_hour": self.start_hour,
            "start_minute": self.start_minute,
            "duration_minutes": self.duration_minutes,
            "end_hour": end_hour,
            "end_minute": end_minute,
            "tz": self.tz.key,
        }


def write_schedule_file(data_dir: Path, schedule: Schedule, limit: int, sources: list[str]) -> Path:
    """Write the active ingestion schedule to ``data_dir/ingestion_schedule.json``.

    The serving stats page reads this file to display the active schedule.
    """
    path = Path(data_dir) / SCHEDULE_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **schedule.to_dict(),
        "limit_per_source": limit,
        "sources": sources,
        "started_at": datetime.now(schedule.tz).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _wait_for_window(
    schedule: Schedule,
    sleep_fn: Callable[[float], None] = time.sleep,
    is_shutdown: Callable[[], bool] = lambda: _shutdown,
) -> None:
    """Block until the schedule's window opens (or shutdown is requested).

    Re-checks ``schedule.in_window()`` after each sleep so that the loop
    actually exits when the window opens — the previous implementation only
    checked ``seconds_until_window > 0``, which is always true and caused the
    process to sleep through every nightly window.
    """
    if schedule.in_window():
        return
    secs = schedule.seconds_until_window()
    logger.info(
        "Outside ingestion window, sleeping %.0f minutes until %02d:%02d %s",
        secs / 60,
        schedule.start_hour,
        schedule.start_minute,
        schedule.tz.key,
    )
    while not is_shutdown() and not schedule.in_window():
        secs = schedule.seconds_until_window()
        sleep_fn(min(60.0, max(1.0, secs)))


def _run_one_cycle(args, schedule: Schedule | None = None) -> int:
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

        if schedule is not None and not schedule.in_window():
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


def build_schedule(args) -> Schedule:
    """Build a Schedule from CLI args."""
    return Schedule(
        start_hour=args.start_hour,
        start_minute=args.start_minute,
        duration_minutes=args.duration,
        tz=ZoneInfo(args.tz),
    )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw


def main():
    parser = argparse.ArgumentParser(
        description="Run ingestion pipeline. Schedule and limit can be set via CLI args "
                    "or matching env vars (INGESTION_START_HOUR, INGESTION_START_MINUTE, "
                    "INGESTION_DURATION_MINUTES, INGESTION_LIMIT_PER_SOURCE, INGESTION_TZ, "
                    "INGESTION_SOURCES).",
    )
    parser.add_argument("--limit", type=int,
                        default=_env_int("INGESTION_LIMIT_PER_SOURCE", DEFAULT_LIMIT_PER_SOURCE),
                        help="Max new documents per source per cycle")
    parser.add_argument("--data-dir", default=_env_str("INGESTION_DATA_DIR", "data"), help="Data directory")
    parser.add_argument("--sources",
                        default=_env_str("INGESTION_SOURCES", "wiki,arxiv,gesetze,pubmed,rki,tagesschau,dw"),
                        help="Comma-separated source names")
    parser.add_argument("--batch-size", type=int, default=3, help="Documents to pull per source per round")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit (ignore time window)")
    parser.add_argument("--start-hour", type=int,
                        default=_env_int("INGESTION_START_HOUR", DEFAULT_START_HOUR),
                        help="Window start hour (0-23)")
    parser.add_argument("--start-minute", type=int,
                        default=_env_int("INGESTION_START_MINUTE", DEFAULT_START_MINUTE),
                        help="Window start minute (0-59)")
    parser.add_argument("--duration", type=int,
                        default=_env_int("INGESTION_DURATION_MINUTES", DEFAULT_DURATION_MINUTES),
                        help="Window duration in minutes")
    parser.add_argument("--tz",
                        default=_env_str("INGESTION_TZ", "Europe/Berlin"),
                        help="Timezone for the schedule")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        total = _run_one_cycle(args, schedule=None)
        logger.info("Total new documents ingested: %d", total)
        return

    schedule = build_schedule(args)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    schedule_path = write_schedule_file(Path(args.data_dir), schedule, args.limit, sources)
    logger.info("Ingestion schedule: %s, limit=%d/source (wrote %s)",
                schedule.describe(), args.limit, schedule_path)

    # Continuous mode: ingest during window, sleep outside it
    while not _shutdown:
        if schedule.in_window():
            logger.info("Ingestion window open (%s), starting cycle", schedule.describe())
            total = _run_one_cycle(args, schedule=schedule)
            logger.info("Cycle complete: %d new documents ingested", total)
            if not _shutdown and schedule.in_window():
                time.sleep(30)  # brief pause between cycles
        else:
            _wait_for_window(schedule)


if __name__ == "__main__":
    main()
