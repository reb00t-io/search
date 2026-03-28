#!/usr/bin/env python3
"""Run the indexing pipeline.

Requires Qdrant to be running (docker compose up qdrant).

Usage:
    python -m indexing.run [--data-dir DIR] [--qdrant-url URL] [--model MODEL]
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from qdrant_client import QdrantClient

from indexing.bm25 import BM25Encoder
from indexing.embedder import get_embedding_dim
from indexing.indexer import create_collection, index_documents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run indexing pipeline")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--qdrant-url", default="http://localhost:6333", help="Qdrant URL")
    parser.add_argument(
        "--model",
        default="intfloat/multilingual-e5-base",
        help="Embedding model name",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)

    # Connect to Qdrant
    client = QdrantClient(url=args.qdrant_url)
    logger.info("Connected to Qdrant at %s", args.qdrant_url)

    # Initialize BM25 encoder
    bm25 = BM25Encoder()
    vocab_path = data_dir / "index" / "bm25_vocab.json"

    # Create collection
    embedding_dim = get_embedding_dim(args.model)
    create_collection(client, embedding_dim)

    # Index documents
    index_documents(client, data_dir, bm25, embedding_model=args.model)

    # Save BM25 vocabulary for query-time use
    bm25.save(vocab_path)
    logger.info("Saved BM25 vocabulary (%d tokens) to %s", len(bm25.vocab), vocab_path)


if __name__ == "__main__":
    main()
