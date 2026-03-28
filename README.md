# Search

A private search engine for AI agents, designed to serve high-quality, trusted content while preventing prompt leakage and injection. Built for deployment in confidential computing (CC) environments.

## Architecture

Continuous pipeline: **Ingest** → **Filter** → **Index** → **Serve**

- **Ingestion:** Wikipedia (DE + EN) via MediaWiki API, arXiv papers via arXiv API. Incremental — tracks ingested IDs, skips duplicates on restart.
- **Filtering:** Quality checks (min length, prose ratio) and safety checks (prompt injection pattern detection). Runs continuously, watches for new data.
- **Indexing:** Hybrid — BM25 (sparse vectors) + dense vector search (multilingual-e5-base), both in Qdrant. Runs continuously, indexes new documents as they appear.
- **Serving:** Hybrid retrieval with RRF fusion, document-level deduplication. REST API + web frontend.

Language focus: German (primary) and English, using multilingual embeddings (`multilingual-e5`) for cross-language retrieval without blanket translation.

**Confidential computing:** The system is designed to run entirely within a CC enclave (out of scope for now). No query data leaves the enclave; only source data downloads are external.

See [docs/spec.md](docs/spec.md) for the full specification.

## Quick Start

```bash
# 1. Allow direnv to load the environment (creates venv, installs deps)
direnv allow

# 2. Start Qdrant
docker compose up -d qdrant

# 3. Run the pipeline (first time — ingests, filters, indexes)
python -m ingestion.run --limit 100
python -m filtering.run --once
python -m indexing.run --rebuild

# 4. Start the web server
python src/main.py
# → http://localhost:31000
```

## Running the Services

### Qdrant (vector database)

```bash
docker compose up -d qdrant       # start
docker compose down               # stop
docker compose restart qdrant     # restart
docker compose logs -f qdrant     # logs
```

### Pipeline stages (each in a separate terminal)

Each stage runs incrementally and continuously — it processes new data as it arrives and waits when caught up. Stop with Ctrl+C.

```bash
# Ingestion: fetches new documents from sources
python -m ingestion.run --limit 100

# Filtering: watches ingested data, filters continuously
python -m filtering.run

# Indexing: watches filtered data, indexes continuously
python -m indexing.run
```

Add `--once` to filtering or indexing to process available data and exit instead of waiting:

```bash
python -m filtering.run --once
python -m indexing.run --once
```

Use `--rebuild` to drop and recreate the Qdrant collection from scratch:

```bash
python -m indexing.run --rebuild
```

### Web server

```bash
python src/main.py                # foreground (Ctrl+C to stop)

./scripts/server.sh start        # background (PID file at data/search.pid)
./scripts/server.sh stop         # stop
./scripts/server.sh restart      # restart
./scripts/server.sh status       # check if running
```

Logs are at `data/server.log` when running in background.

### All-in-one (background)

```bash
docker compose up -d qdrant
python -m ingestion.run --limit 100
python -m filtering.run --once
python -m indexing.run --once
python src/main.py
```

## Search API

```
GET /v1/search?q=query&lang=de&source=wiki&mode=hybrid&group_by=docs&limit=10
```

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `q` | string | required | Search query |
| `lang` | `de`, `en`, `all` | `all` | Language filter |
| `source` | `wiki`, `arxiv`, `all` | `all` | Source filter |
| `mode` | `hybrid`, `bm25`, `vector` | `hybrid` | Retrieval mode |
| `group_by` | `docs`, `chunks` | `docs` | `docs` = one result per document; `chunks` = all chunks |
| `limit` | 1-100 | 10 | Results per page |
| `offset` | int | 0 | Pagination offset |

## Project Structure

```
ingestion/               # Stage 1: fetch + store documents
  wikipedia.py           #   Wikipedia adapter (MediaWiki API)
  arxiv_adapter.py       #   arXiv adapter (arXiv API)
  storage.py             #   Content-hash storage
  cursor.py              #   Incremental cursor utilities
  run.py                 #   CLI entry point

filtering/               # Stage 2: quality + safety filters
  filters.py             #   Filter implementations
  run.py                 #   CLI entry point (continuous)

indexing/                # Stage 3: embed + index into Qdrant
  embedder.py            #   Dense embeddings (multilingual-e5)
  bm25.py                #   BM25 sparse encoder
  indexer.py             #   Qdrant collection management
  run.py                 #   CLI entry point (continuous)

serving/                 # Stage 4: search API
  search.py              #   Hybrid retrieval + deduplication

src/                     # Web server (Quart)
  main.py                #   App entry point + /v1/search endpoint
  templates/index.html   #   Search frontend

test/                    # Tests for all stages
data/                    # Runtime data (gitignored)
  content/               #   Markdown files by content hash
  ingested/              #   JSONL metadata records
  filtered/              #   Filtered records
  cursors/               #   Incremental processing state
  index/                 #   BM25 vocabulary
```
