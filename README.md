# Search

A private search engine for AI agents, designed to serve high-quality, trusted content while preventing prompt leakage and injection. Built for deployment in confidential computing (CC) environments.

## Architecture

Continuous pipeline: **Ingest** → **Filter** → **Index** → **Serve**

- **Ingestion:** 7 sources (Wikipedia DE+EN, arXiv, German federal law, PubMed, RKI, Tagesschau, Deutsche Welle). Round-robin fetching across sources. Incremental — tracks ingested IDs, skips duplicates on restart. Documents tagged as `abstract` or `full_text` with links to full text where available.
- **Filtering:** Quality checks (min length, prose ratio) and safety checks (prompt injection pattern detection). Runs continuously, watches for new data.
- **Indexing:** Hybrid — BM25 (sparse vectors) + dense vector search (qwen3-embedding-4b, 1024-dim via Privatemode API), both in Qdrant. Runs continuously, indexes new documents as they appear.
- **Serving:** Hybrid retrieval with RRF fusion, document-level deduplication. REST API + web frontend + chat interface.

Language focus: German (primary) and English, using multilingual embeddings for cross-language retrieval.

**Confidential computing:** Designed to run entirely within a CC enclave (out of scope for now). No query data leaves the enclave; only source data downloads are external.

See [docs/spec.md](docs/spec.md) for the full specification.

## Data Sources

| Source | Content | Language | Type | Full text |
|--------|---------|----------|------|-----------|
| Wikipedia | Encyclopedia articles | DE + EN | full_text | -- |
| arXiv | Scientific papers | EN | abstract | [arxiv.org/html](https://arxiv.org) |
| Gesetze | German federal laws | DE | full_text | -- |
| PubMed | Biomedical literature | EN | abstract | DOI links |
| RKI | Public health reports | DE | abstract | edoc.rki.de |
| Tagesschau | German news (ARD) | DE | full_text | -- |
| Deutsche Welle | German news | DE | full_text | -- |

## Quick Start

```bash
# 1. Allow direnv to load the environment (creates venv, installs deps)
direnv allow

# 2. Start Qdrant
docker compose up -d qdrant

# 3. Run the pipeline (first time — ingests, filters, indexes)
python -m ingestion.run --limit 200
python -m filtering.run --once
python -m indexing.run --rebuild

# 4. Start the web server
python src/main.py
# → http://localhost:31000       (search UI)
# → http://localhost:31000/chat  (chat UI)

# Stats
python -m indexing.run --stats
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
# Ingestion: round-robin across all sources, 3 docs per source per round
python -m ingestion.run --limit 200
python -m ingestion.run --limit 200 --sources wiki,arxiv   # specific sources
python -m ingestion.run --limit 200 --batch-size 5         # larger batches

# Filtering: watches ingested data, filters continuously
python -m filtering.run

# Indexing: watches filtered data, indexes continuously
python -m indexing.run
```

Add `--once` to filtering or indexing to process available data and exit:

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

## Search API

```
GET /v1/search?q=query&lang=de&source=wiki&mode=hybrid&group_by=docs&limit=10
```

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `q` | string | required | Search query |
| `lang` | `de`, `en`, `all` | `all` | Language filter |
| `source` | `wiki`, `arxiv`, `gesetze`, `pubmed`, `rki`, `tagesschau`, `dw`, `all` | `all` | Source filter |
| `mode` | `hybrid`, `bm25`, `vector` | `hybrid` | Retrieval mode |
| `group_by` | `docs`, `chunks` | `docs` | `docs` = one result per document; `chunks` = all chunks (for agents) |
| `limit` | 1-100 | 10 | Results per page |
| `offset` | int | 0 | Pagination offset |

Results include `content_type` ("abstract" or "full_text") and `full_text_url` (link to full text, if available).

## Pages

- `/` — Search UI with filters, result highlights, and pagination
- `/chat` — Chat interface backed by the search index (LLM uses `web_search` tool)

## Project Structure

```
ingestion/               # Stage 1: fetch + store documents
  wikipedia.py           #   Wikipedia (MediaWiki API, DE + EN)
  arxiv_adapter.py       #   arXiv (API, abstracts)
  gesetze.py             #   German federal law (gesetze-im-internet.de XML)
  pubmed.py              #   PubMed (NCBI E-utilities, abstracts)
  rki.py                 #   RKI (OAI-PMH, edoc.rki.de)
  rss_adapter.py         #   RSS feeds (Tagesschau, Deutsche Welle)
  base.py                #   Document model + SourceAdapter interface
  chunking.py            #   Shared text chunking (heading/paragraph/sentence)
  storage.py             #   Content-hash storage
  cursor.py              #   Incremental cursors (JSONL offset, ID set)
  run.py                 #   CLI: round-robin ingestion across sources

filtering/               # Stage 2: quality + safety filters
  filters.py             #   Quality (length, prose) + safety (injection patterns)
  run.py                 #   CLI: continuous or --once

indexing/                # Stage 3: embed + index into Qdrant
  embedder.py            #   Privatemode API (qwen3-embedding-4b, 1024-dim)
  bm25.py                #   BM25 sparse encoder (DE + EN stop words)
  indexer.py             #   Qdrant: dense + sparse vectors, payload indices
  run.py                 #   CLI: continuous, --once, --rebuild, --stats

serving/                 # Stage 4: search API
  search.py              #   Hybrid retrieval + RRF + doc deduplication

src/                     # Web server (Quart)
  main.py                #   Routes: /, /chat, /v1/search, /v1/responses
  templates/index.html   #   Search frontend
  templates/chat.html    #   Chat frontend
  static/chat/chat.js    #   Chat client (streaming, markdown, tools)
  tool_executor.py       #   web_search → local search service
  streaming.py           #   LLM streaming + tool execution loop

test/                    # Tests (85 passing)
data/                    # Runtime data (gitignored)
  content/               #   Markdown files by content hash
  ingested/              #   JSONL metadata records
  filtered/              #   Filtered records
  cursors/               #   Incremental processing state
  index/                 #   BM25 vocabulary
```
