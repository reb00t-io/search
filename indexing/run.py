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
from indexing.indexer import create_collection, ensure_collection, index_documents, index_records

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Shutdown signal received, finishing current batch...")
    _shutdown = True


def main():
    parser = argparse.ArgumentParser(description="Run indexing pipeline")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--qdrant-url", default="http://localhost:6333", help="Qdrant URL")
    parser.add_argument("--model", default="intfloat/multilingual-e5-base", help="Embedding model name")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between polls for new data")
    parser.add_argument("--once", action="store_true", help="Process available data and exit (don't wait)")
    parser.add_argument("--rebuild", action="store_true", help="Drop collection and reindex everything from scratch")
    args = parser.parse_args()

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
    embedding_dim = get_embedding_dim(args.model)

    if args.rebuild:
        logger.info("Rebuild mode: dropping and recreating collection")
        create_collection(client, embedding_dim)
        index_documents(client, data_dir, bm25, embedding_model=args.model)
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
            indexed = index_records(client, new_records, bm25, store.read_content, args.model)
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
