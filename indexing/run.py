#!/usr/bin/env python3
"""Run the indexing pipeline continuously.

Watches filtered/documents.jsonl for new records, embeds and indexes them
into Qdrant. Waits for new data when caught up.

Usage:
    python -m indexing.run [--data-dir DIR] [--qdrant-url URL] [--model MODEL] [--poll-interval SEC] [--once]
    python -m indexing.run --rebuild   # Drop and recreate collection, reindex everything
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from pathlib import Path

from qdrant_client import QdrantClient

from indexing.bm25 import BM25Encoder
from indexing.embedder import get_embedding_dim
from indexing.indexer import COLLECTION_NAME, create_collection, ensure_collection, index_documents, index_records

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Shutdown signal received, finishing current batch...")
    _shutdown = True


def _fmt_bytes(b: int) -> str:
    if b >= 1 << 30:
        return f"{b / (1 << 30):.2f} GB"
    if b >= 1 << 20:
        return f"{b / (1 << 20):.1f} MB"
    if b >= 1 << 10:
        return f"{b / (1 << 10):.0f} KB"
    return f"{b} B"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _print_stats(qdrant_url: str, data_dir: Path):
    """Print index statistics and exit."""
    client = QdrantClient(url=qdrant_url)
    try:
        info = client.get_collection(COLLECTION_NAME)
    except Exception:
        print("Collection not found. Run indexing first.")
        return

    points = info.points_count or 0
    segments = info.segments_count or 0

    # Count by source and content_type from filtered JSONL
    import json
    from collections import Counter

    filtered_path = data_dir / "filtered" / "documents.jsonl"
    source_counts: Counter = Counter()
    ct_counts: Counter = Counter()
    source_ct: Counter = Counter()
    if filtered_path.exists():
        for line in filtered_path.read_text().strip().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                src = rec.get("source", "?")
                ct = rec.get("content_type", "full_text")
                source_counts[src] += 1
                ct_counts[ct] += 1
                source_ct[f"{src}:{ct}"] += 1
            except json.JSONDecodeError:
                pass

    content_bytes = _dir_size(data_dir / "content")
    total_bytes = _dir_size(data_dir)

    filtered_total = sum(source_counts.values())
    print(f"Ingested:      {filtered_total}")
    print(f"  full_text:   {ct_counts.get('full_text', 0)}")
    print(f"  abstract:    {ct_counts.get('abstract', 0)}")
    print(f"Indexed:       {points}" + (f"  (run --once or --rebuild to index {filtered_total - points} pending)" if points < filtered_total else ""))
    print(f"Segments:      {segments}")
    print(f"Content files: {_fmt_bytes(content_bytes)}")
    print(f"Data dir:      {_fmt_bytes(total_bytes)}")
    print()
    print("By source:")
    for src in sorted(source_counts):
        total = source_counts[src]
        ft = source_ct.get(f"{src}:full_text", 0)
        ab = source_ct.get(f"{src}:abstract", 0)
        parts = []
        if ft:
            parts.append(f"{ft} full")
        if ab:
            parts.append(f"{ab} abstract")
        detail = f"  ({', '.join(parts)})" if parts else ""
        print(f"  {src:<12} {total:>5}{detail}")


def main():
    parser = argparse.ArgumentParser(description="Run indexing pipeline")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--qdrant-url", default="http://localhost:6333", help="Qdrant URL")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between polls for new data")
    parser.add_argument("--once", action="store_true", help="Process available data and exit (don't wait)")
    parser.add_argument("--rebuild", action="store_true", help="Drop collection and reindex everything from scratch")
    parser.add_argument("--stats", action="store_true", help="Print index statistics and exit")
    args = parser.parse_args()

    if args.stats:
        _print_stats(args.qdrant_url, Path(args.data_dir))
        return

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    data_dir = Path(args.data_dir)
    vocab_path = data_dir / "index" / "bm25_vocab.json"

    # Connect to Qdrant
    client = QdrantClient(url=args.qdrant_url)
    logger.info("Connected to Qdrant at %s", args.qdrant_url)

    # Initialize BM25 encoder (load existing vocab if available)
    bm25 = BM25Encoder()
    bm25.load(vocab_path)

    # Get embedding dim (loads model on first call)
    embedding_dim = get_embedding_dim()

    if args.rebuild:
        logger.info("Rebuild mode: dropping and recreating collection")
        create_collection(client, embedding_dim)
        index_documents(client, data_dir, bm25)
        bm25.save(vocab_path)
        logger.info("Rebuild complete. BM25 vocab: %d tokens", len(bm25.vocab))
        return

    # Incremental mode: ensure collection exists, then watch for new data
    ensure_collection(client, embedding_dim)

    from ingestion.cursor import JsonlCursor
    from ingestion.storage import ContentStore

    store = ContentStore(args.data_dir)
    filtered_path = data_dir / "filtered" / "documents.jsonl"
    cursor = JsonlCursor(data_dir / "cursors" / "indexing.json")

    logger.info("Indexing starting (offset=%d, poll=%.1fs, once=%s)", cursor.offset, args.poll_interval, args.once)

    total_indexed = 0

    while not _shutdown:
        new_records = cursor.read_new_lines(filtered_path)

        if new_records:
            indexed = index_records(client, new_records, bm25, store.read_content)
            cursor.save()
            bm25.save(vocab_path)
            total_indexed += indexed
            logger.info("Indexed %d new documents (total this run: %d, vocab: %d tokens)",
                        indexed, total_indexed, len(bm25.vocab))
        else:
            if args.once:
                break
            time.sleep(args.poll_interval)

    bm25.save(vocab_path)
    logger.info("Indexing stopped. Total indexed this run: %d", total_indexed)


if __name__ == "__main__":
    main()
