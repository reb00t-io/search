# Search Engine Specification

## Summary

A private, agent-oriented search engine with a continuous ingestion-to-serving pipeline. Designed to run in confidential computing (CC) environments to prevent prompt leakage, and to serve only trusted, filtered content to minimize prompt injection risk. The system ingests high-quality sources (starting with Wikipedia DE/EN and arXiv), converts content to markdown, builds a hybrid search index (BM25 + vector), and serves results via a reranking API. A web frontend exists for testing.

**Key design decisions:**
- **Data sources (MVP):** Wikipedia (DE + EN) + arXiv papers; extensible source adapter architecture
- **Language strategy:** Index in original language; use multilingual embeddings for cross-language retrieval; translate high-value EN content to DE on-demand via LLM (not blanket translation)
- **Search:** Hybrid retrieval (BM25 + multilingual vector search) with cross-encoder reranking
- **Freshness:** Continuous pipeline — periodic dump re-ingestion + real-time change stream processing
- **Security:** Trusted sources only, content filtering for injection patterns, CC deployment (future)

---

## Stage 1: Ingestion

### 1.1 Source Adapter Architecture

Each data source is implemented as a **source adapter** — a Python class that implements a common interface:

```python
class SourceAdapter:
    name: str                          # e.g. "wiki", "arxiv"
    def bulk_ingest(self) -> Iterator[Document]:
        """Initial/periodic full ingestion from dumps or bulk APIs."""
    def stream_updates(self) -> Iterator[Document]:
        """Continuous stream of new/changed documents. May be a no-op."""
    def document_url(self, doc_id: str) -> str:
        """Canonical URL for a document."""
```

All adapters emit the same `Document` format (see 1.4), so the downstream pipeline (filter → index → serve) is source-agnostic. Adding a new source = writing a new adapter + registering it.

### 1.2 MVP Source: Wikipedia (DE + EN)

- High quality, structured, freely available
- Both DE (~2.8M articles) and EN (~6.8M articles) available as dumps
- Real-time change stream available (Wikimedia EventStreams)

**Bulk ingestion:**
- Download from `dumps.wikimedia.org`: `dewiki-latest-pages-articles.xml.bz2`, `enwiki-latest-pages-articles.xml.bz2`
- New dumps published roughly every 2 weeks
- Stream-parse the XML dump (avoid loading into memory — files are 5-20 GB compressed)
- For each article:
  - Extract title, wikitext, categories, last-modified timestamp
  - Parse wikitext to markdown using `mwparserfromhell` + custom renderer
  - Strip templates, infoboxes, and navigation elements (keep prose + headings + lists + tables)
  - Split long articles into chunks (~500-1000 tokens each) at heading boundaries
  - Assign a stable document ID: `wiki:{lang}:{page_id}:{chunk_index}`

**Real-time updates:**
- SSE endpoint: `https://stream.wikimedia.org/v2/stream/recentchange`
- Filter for `dewiki` and `enwiki` namespaces
- On change event: fetch updated article via MediaWiki API (`action=parse`), re-extract, re-index
- Store a cursor/timestamp to resume after restarts
- Re-ingest full dumps monthly as a consistency checkpoint

**Tools/libraries:**
- `mwxml` or `xml.etree.ElementTree` (incremental XML parsing)
- `mwparserfromhell` (wikitext → AST)
- Custom AST-to-markdown renderer (wikitext templates → clean markdown)

### 1.3 MVP Source: arXiv

arXiv provides open-access scientific papers (~2.5M papers, growing ~15k/month). Primarily English, covering physics, math, CS, biology, finance, and more.

**Bulk ingestion:**
- **Option A — arXiv Bulk Data Access (preferred):** S3 requester-pays bucket `s3://arxiv` contains all paper source files (LaTeX) and metadata. Use the metadata snapshot (`arxiv-metadata-oai-snapshot.json`, ~4 GB) for initial load.
- **Option B — OAI-PMH API:** Harvest metadata incrementally via `https://export.arxiv.org/oai2`. Rate-limited but doesn't require AWS credentials. Good for smaller subsets or as a freshness mechanism.
- For each paper:
  - Extract title, authors, abstract, categories, submission date, paper ID
  - Use the abstract as the primary text (full-text extraction from LaTeX/PDF is complex — defer to post-MVP)
  - For papers with HTML versions (arXiv HTML5, growing coverage): fetch and extract full text
  - Assign stable document ID: `arxiv:{paper_id}:{chunk_index}` (e.g., `arxiv:2301.07041:0`)

**Parsing pipeline:**
1. Parse metadata JSON (one record per line in the bulk snapshot)
2. Extract: `id`, `title`, `authors`, `abstract`, `categories`, `update_date`
3. Concatenate title + abstract as the document text (most arXiv abstracts are 150-300 words — typically one chunk)
4. For papers with multiple versions, use the latest version
5. Output markdown:
   ```markdown
   # {title}

   **Authors:** {authors}
   **Categories:** {categories}

   {abstract}
   ```

**Real-time updates:**
- Poll arXiv RSS feeds per category (e.g., `https://rss.arxiv.org/rss/cs.AI`) — updated daily
- Or use OAI-PMH with `from` date parameter for incremental harvesting
- New papers appear with ~1 day delay
- Store last-harvested date per category to resume after restarts

**Full-text extraction (post-MVP enhancement):**
- arXiv is increasingly publishing HTML5 versions of papers (ar5iv project)
- When available, fetch HTML and extract full text via BeautifulSoup
- For LaTeX-only papers: use `pandoc` or `latex2text` for conversion (noisy but usable)
- Split full papers into chunks at section boundaries

**Rate limits & etiquette:**
- arXiv API: max 1 request/3 seconds, include contact email in User-Agent
- S3 bulk access: no rate limit but requester pays for bandwidth
- RSS: no strict limit, poll at most once per hour

**Tools/libraries:**
- `arxiv` Python package (API wrapper) or direct HTTP for OAI-PMH
- `boto3` for S3 bulk data access (optional)
- `feedparser` for RSS polling
- `beautifulsoup4` (already a dependency) for HTML extraction

### 1.4 Common Document Format

Source adapters emit metadata as JSONL and markdown content as separate files. Content is stored by SHA-256 hash, which provides automatic deduplication — identical content from different sources or document versions results in a single file.

**JSONL metadata record:**
```json
{
  "id": "wiki:de:12345:0",
  "source": "wiki",
  "title": "Article Title",
  "url": "https://de.wikipedia.org/wiki/...",
  "language": "de",
  "content_hash": "a1b2c3d4e5f6...",
  "metadata": {
    "categories": ["Category:Science"],
    "authors": null,
    "chunk_index": 0,
    "total_chunks": 5
  },
  "timestamp": "2025-03-15T10:00:00Z"
}
```

**Markdown content file** (referenced by `content_hash`):
```
data/content/a1/b2/a1b2c3d4e5f6...md
```

Content files are sharded by the first two pairs of the hash (like git objects) to avoid huge flat directories. Files are immutable — a given hash always maps to the same content.

**Update handling:** When a document is updated (e.g., Wikipedia edit), the adapter writes the new content file (new hash), then updates the JSONL record to point to the new hash. The old content file may become orphaned if no other document references it. A periodic GC pass scans all JSONL records, collects referenced hashes, and removes unreferenced content files.

Top-level fields are required for all sources. The `metadata` dict holds source-specific fields (categories for Wikipedia, authors/categories for arXiv, etc.). The `source` field enables source-specific filtering at query time.

### 1.5 Storage

Source adapters manage their own cursor state (for resumable ingestion) but all emit documents into a shared pipeline directory. Storage is source-agnostic from this point forward.

```
data/
  content/             # markdown files by content hash (aa/bb/aabb....md)
  ingested/
    documents.jsonl    # metadata records pointing to content hashes
  updates/
    pending.jsonl      # queued real-time updates from all sources
  cursors/             # per-source resume state (managed by adapters)
    wiki_de.json
    wiki_en.json
    arxiv.json
```

### 1.6 Future Sources (post-MVP)

- Curated domain lists (e.g., government sites, academic publishers, documentation sites)
- Selected RSS feeds from trusted publishers
- CC search users can add their custom pages (won't be distributed to other users); those requests are confidential such that nobody will ever know what sites a certain customer uses; they will be fetched delayed together with other sites so anyone listening on our network cannot easily correlate a fetched site to a customer

Adding a new source requires only:
1. Implement a `SourceAdapter` subclass
2. Register it in the ingestion config
3. No changes to filter, index, or serving layers

---

## Stage 2: Filtering

Filtering operates on the common document format (see 1.4). It is fully source-agnostic — filters see only the standardized fields, not the source-specific details.

### 2.1 Quality Filtering

Remove low-value documents before indexing:

- **Short content:** Drop documents below a minimum text length (e.g., <200 chars after extraction)
- **Low prose ratio:** Drop documents that are purely lists, tables, or navigation with no prose
- **Near-duplicate detection:** Exact duplicates are already eliminated by content-hash storage (see 1.4). This filter catches near-duplicates (e.g., minor formatting differences) via simhash or minhash

Source-specific quality issues (e.g., Wikipedia disambiguation pages, redirect pages) should be handled during extraction in the source adapter, before documents enter the shared pipeline.

### 2.2 Safety Filtering

Detect and remove content that could be used for prompt injection:

- **Pattern matching:** Scan for known injection patterns:
  - Instruction-like phrases: "ignore previous instructions", "you are now", "system:", "assistant:"
  - Markdown/HTML injection: hidden text, zero-width characters, homoglyph attacks
  - Base64-encoded payloads in otherwise normal text
- **Anomaly scoring:** Flag documents with unusual token distributions (e.g., high density of control-like language in otherwise encyclopedic/scientific text)
- **Allowlist approach:** Since we control the sources, the primary defense is source trust. Filtering is a defense-in-depth layer.

### 2.3 Output

Filtering reads metadata records from `ingested/documents.jsonl` and content from `data/content/`. Accepted and rejected records are written as metadata-only JSONL (content files are shared, not copied):

```
data/
  filtered/
    documents.jsonl    # accepted metadata records, ready for indexing
  rejected/
    rejected.jsonl     # rejected metadata records, with rejection reason
```

---

## Stage 3: Indexing

**Single backend: Qdrant** — handles both dense vector search and BM25 (sparse vectors) in one service. Since v1.15.2, Qdrant computes BM25 sparse vectors server-side from raw text, eliminating the need for a separate full-text search engine. See [Qdrant sparse retrieval docs](https://qdrant.tech/course/essentials/day-3/sparse-retrieval-demo/).

### 3.1 Qdrant Collection Schema

One collection with both dense and sparse vector indices:

**Dense vectors (semantic search):**
- Embedding model: `intfloat/multilingual-e5-large` (1024-dim) or `multilingual-e5-base` (768-dim, lower resource usage)
- Handles DE and EN natively — no translation needed for cross-language retrieval
- Index type: HNSW (approximate nearest neighbor)

**Sparse vectors (BM25):**
- Qdrant's native BM25 computation from raw text
- Snowball stemmer with language-specific stop word filtering
- IDF maintained at collection level by Qdrant
- Parameters: `k1=1.2`, `b=0.75` (tunable), average document length provided at index time

**Payload (metadata):**
- `id` (string) — stable document ID, used as Qdrant point ID
- `title` (string, indexed)
- `text` (string) — full chunk text, also used as BM25 input
- `language` (string, indexed) — `"de"` or `"en"`
- `url` (string)
- `timestamp` (datetime, indexed)
- `categories` (string[], indexed)
- `chunk_index` (int)
- `total_chunks` (int)

### 3.2 Indexing Pipeline

For each filtered document chunk:
1. Compute dense embedding via `multilingual-e5` (locally via `sentence-transformers`)
2. Upsert into Qdrant with:
   - Dense vector (embedding)
   - Raw text for BM25 (Qdrant computes sparse vector server-side)
   - Metadata payload
3. On updates: upsert by point ID — Qdrant overwrites existing points

### 3.3 Index Updates

- **Full rebuild:** Triggered after dump re-ingestion (monthly). Create new collection, populate, swap alias, delete old collection (zero-downtime).
- **Incremental:** Real-time updates flow through: ingest → filter → embed → upsert into Qdrant.
- **Consistency:** Each document has a `timestamp`. On conflict, latest timestamp wins.

### 3.4 Scaling

- Qdrant supports distributed mode: sharding across nodes + replication (Raft consensus)
- Disk-backed storage (mmap) for datasets exceeding RAM
- Scalar quantization (float32→uint8) reduces memory 4x with minimal quality loss
- For terabyte-scale: 3-5 nodes with disk-backed HNSW, sharded collections

---

## Stage 4: Serving

### 4.1 Search API

REST API served by the existing Quart backend.

**Endpoint:** `GET /v1/search`

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | required | Search query |
| `lang` | string | `"de"` | Preferred language (`de`, `en`, `all`) |
| `source` | string | `"all"` | Filter by source (`all`, `wiki`, `arxiv`) |
| `mode` | string | `"hybrid_rerank"` | Retrieval mode (`hybrid_rerank`, `hybrid`, `bm25`, `vector`) |
| `limit` | int | `10` | Number of results |
| `offset` | int | `0` | Pagination offset |

**Response:**
```json
{
  "query": "...",
  "results": [
    {
      "id": "wiki:de:12345:0",
      "title": "...",
      "url": "...",
      "snippet": "...matched text with highlights...",
      "language": "de",
      "score": 0.87,
      "timestamp": "2025-03-15T10:00:00Z"
    }
  ],
  "total": 142,
  "took_ms": 45
}
```

### 4.2 Hybrid Retrieval

For each query:

1. **BM25 retrieval:** Query Qdrant sparse index (native BM25), retrieve top-K candidates (K=50)
2. **Vector retrieval:** Embed query with multilingual-e5, query Qdrant dense index for top-K nearest (K=50)
3. **Fusion:** Qdrant supports hybrid queries combining dense + sparse in a single request with Reciprocal Rank Fusion (RRF):
   ```
   RRF_score(d) = sum(1 / (k + rank_i(d))) for each retrieval method i
   ```
   where `k=60` (standard constant)
4. **Reranking:** Take top-N fused results (N=20), rerank with a cross-encoder:
   - Model: `cross-encoder/ms-marco-MiniLM-L-12-v2` (or a multilingual variant)
   - Input: `(query, document_text)` pairs
   - Output: relevance score per pair
5. **Return:** Top results after reranking, with snippets extracted around matched terms

### 4.3 Language Handling at Query Time

- If `lang=de`: Boost German results (Qdrant payload filter + RRF weight adjustment), but still include relevant EN results
- If `lang=en`: Boost English results
- If `lang=all`: No language preference
- Multilingual embeddings handle cross-language retrieval naturally — a German query finds relevant English documents without translation

### 4.4 Agent API

For agent consumption (the primary use case), add a structured endpoint:

**Endpoint:** `GET /v1/agent/search`

Same parameters as `/v1/search`, but response optimized for LLM context:
```json
{
  "query": "...",
  "context": "## Result 1: Title\nSource: url\n\nRelevant text...\n\n---\n\n## Result 2: ...",
  "result_count": 5
}
```

Returns pre-formatted markdown context block ready for injection into an agent's prompt, with source attribution.

---

## Stage 5: Frontend

Replace the existing template page (`src/templates/index.html`) with a search interface for testing and quality evaluation. The chat panel is removed — the search UI is the primary interface.

### 5.1 Layout

Clean, minimal search page — Google-style centered layout:

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│                      Search                             │
│            ┌──────────────────────────┐                 │
│            │ query input              │  [Search]       │
│            └──────────────────────────┘                 │
│         [DE ▾]  [EN ▾]  [All sources ▾]  [Mode ▾]      │
│                                                         │
│  ─────────────────────────────────────────────────      │
│  About 142 results (45ms)                               │
│                                                         │
│  Article Title                               DE  wiki   │
│  https://de.wikipedia.org/wiki/...                      │
│  ...matched text with **highlights**...                 │
│  Score: 0.87  │  2025-03-15                             │
│                                                         │
│  Another Article Title                       EN  arxiv  │
│  https://arxiv.org/abs/2301.07041                       │
│  ...matched text with **highlights**...                 │
│  Score: 0.82  │  2025-01-17                             │
│                                                         │
│  [< 1 2 3 4 5 ... >]                                   │
│                                                         │
│  ─────────────────────────────────────────────────      │
│  v0.1.0                                                 │
└─────────────────────────────────────────────────────────┘
```

### 5.2 Components

**Search bar:**
- Large centered text input, autofocus on load
- Submit on Enter or button click
- Query preserved in URL (`?q=...&lang=...&mode=...`) for shareability

**Filters (below search bar):**
- **Language:** dropdown — DE (default), EN, All
- **Source:** dropdown — All (default), Wikipedia, arXiv
- **Retrieval mode:** dropdown — Hybrid+Rerank (default), Hybrid (no rerank), BM25 only, Vector only
  - Mode selector is for evaluation/debugging — lets you compare retrieval quality

**Results list:**
- Each result shows: title (linked), URL, text snippet with query term highlights, relevance score, timestamp
- Language badge (DE/EN) and source badge (wiki/arxiv) on each result
- Pagination at bottom

**Status bar:**
- Result count and query latency (from API `took_ms`)
- Error display if search fails

### 5.3 API Integration

The frontend calls `GET /v1/search` with query parameters. The `mode` filter maps to an additional API parameter:

| Frontend mode | API behavior |
|---|---|
| Hybrid+Rerank | Full pipeline: BM25 + vector + RRF + cross-encoder rerank |
| Hybrid | BM25 + vector + RRF, skip reranking |
| BM25 only | Qdrant sparse query only |
| Vector only | Qdrant dense query only |

Add `mode` parameter to the search API: `mode=hybrid_rerank` (default), `hybrid`, `bm25`, `vector`.

### 5.4 Implementation

- Single `index.html` Jinja template (replaces the current template)
- Vanilla JS (no framework, no build step) — same approach as the existing codebase
- Responsive: works on desktop and mobile
- No external dependencies beyond what the browser provides

---

## Language Strategy: Recommendation

**Approach: Multilingual embeddings + selective translation**

1. **Index everything in its original language.** Both DE and EN Wikipedia are indexed as-is.
2. **Multilingual embeddings handle cross-language retrieval.** `multilingual-e5` maps DE and EN into the same vector space. A German query naturally retrieves relevant English documents.
3. **BM25 remains language-specific.** Lexical matching works within a language. The hybrid approach compensates — vector search covers cross-language, BM25 covers exact term matching.
4. **Selective LLM translation (future enhancement):**
   - For high-traffic queries where the best results are in EN but user wants DE: translate the top-N result snippets on-the-fly via LLM
   - Cache translations to avoid repeated work
   - Do NOT blanket-translate all EN→DE: too expensive (~6.8M articles), introduces translation artifacts, and multilingual embeddings already solve the retrieval problem
5. **Query-time translation:** Translate DE queries to EN and run a second BM25 pass. Fuse results.

**Why not translate everything?**
- Cost: translating 6.8M EN articles via LLM is prohibitively expensive
- Quality: LLM translations introduce artifacts and errors, especially for technical/domain-specific content
- Staleness: translations become outdated when source articles change
- Multilingual embeddings solve 80% of the cross-language problem at zero marginal cost

---

## Pipeline Orchestration

The pipeline runs as a set of long-running processes, coordinated via a simple scheduler:

```
┌─────────────┐     ┌──────────┐     ┌──────────┐     ┌─────────┐
│  Ingestion  │────>│  Filter  │────>│  Index   │────>│  Serve  │
│  (dumps +   │     │ (quality │     │ (Qdrant: │     │  (API + │
│   stream)   │     │ + safety)│     │ BM25+vec)│     │  web UI)│
└─────────────┘     └──────────┘     └──────────┘     └─────────┘
      │                                                      │
      └──── real-time updates flow through continuously ─────┘
```

**Processes:**
1. **Dump ingester:** Runs on schedule (monthly), downloads latest dumps, extracts, outputs JSONL
2. **Stream listener:** Runs continuously, listens to EventStreams, queues updates
3. **Filter worker:** Processes pending documents from ingestion or stream
4. **Index updater:** Picks up filtered documents, embeds and upserts into Qdrant (dense + sparse vectors)
5. **API server:** The existing Quart app, extended with search endpoints

**Orchestration options:**
- Simple: Python scripts with `supervisord` or systemd units inside the Docker container
- Better: Separate Docker services in `docker-compose.yml` with shared volume for data

---

## Infrastructure & Dependencies

### New Python dependencies
```
# Ingestion — Wikipedia
mwparserfromhell          # wikitext parsing
mwxml                     # Wikipedia dump XML parsing

# Ingestion — arXiv
feedparser                # RSS feed parsing for arXiv updates
boto3                     # S3 access for arXiv bulk data (optional)

# Search & Indexing
qdrant-client             # Qdrant client (dense vectors + native BM25 sparse vectors)
sentence-transformers     # Embedding model inference + cross-encoder reranking
torch                     # ML framework (for sentence-transformers)
```

### New Docker services (docker-compose.yml)
```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333"]
    volumes: ["qdrant_data:/qdrant/storage"]

  search:
    # existing app, extended
    depends_on: [qdrant]
```

### Hardware considerations
- **Embedding model:** `multilingual-e5-base` runs on CPU (slower) or GPU. For MVP, CPU is fine (~100 docs/sec).
- **Storage:** Wikipedia DE dumps ~5 GB compressed, EN ~22 GB compressed. arXiv metadata ~4 GB. Extracted JSONL + indices: estimate ~50-100 GB total.
- **Qdrant:** ~10M vectors at 768-dim ≈ 30 GB RAM (with HNSW index). Can use disk-backed mode for lower RAM.
- **For CC deployment:** All components must run within the enclave. No external API calls except for source data download and LLM translation endpoint.
