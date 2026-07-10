#!/usr/bin/env python3
"""One-time migration: re-chunk the gesetze source per § (2026-07).

Purges all gesetze records from the pipeline files and Qdrant and resets the
gesetze ingestion cursor, so the next ingestion run re-fetches every law with
the new §-aligned chunking (ingestion/gesetze.py) and the new BM25 §-tokens
(indexing/bm25.py) apply to the re-indexed chunks.

Run on the host with the pipeline services STOPPED:

    cd ~/search
    docker compose stop ingestion filtering indexing
    docker compose run --rm --no-deps --entrypoint python indexing \
        scripts/reingest_gesetze.py --data-dir /data --qdrant-url http://qdrant:6333
    docker compose up -d
    # one-shot full re-ingest instead of waiting for nightly windows:
    docker compose run --rm --no-deps --entrypoint python ingestion \
        -m ingestion.run --once --sources gesetze --limit 1000000 --data-dir /data
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.maintenance import delete_source_points, purge_source  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--source", default="gesetze")
    parser.add_argument("--skip-qdrant", action="store_true",
                        help="only rewrite files/cursors, keep Qdrant points")
    args = parser.parse_args()

    stats = purge_source(args.data_dir, args.source)
    if not args.skip_qdrant:
        delete_source_points(args.qdrant_url, args.source)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
