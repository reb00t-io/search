# TODO

## Ingestion
- [ ] arXiv: full-text extraction (currently abstracts only). Use ar5iv HTML versions where available, fall back to LaTeX/pandoc conversion.
- [ ] Wikipedia: switch from API to dump-based ingestion for full corpus
- [ ] Wikipedia: real-time updates via Wikimedia EventStreams SSE
- [ ] arXiv: incremental updates via OAI-PMH or RSS polling
- [ ] Content GC: periodic cleanup of orphaned content-hash files

## Filtering
- [ ] Near-duplicate detection (simhash/minhash) across sources
- [ ] Anomaly scoring for unusual token distributions

## Indexing
- [ ] Zero-downtime reindex (create new collection, swap alias, delete old)
- [ ] Evaluate SPLADE++ as an alternative to BM25 sparse vectors

## Serving
- [ ] Cross-encoder reranking (hybrid_rerank mode)
- [ ] Query-time translation: translate DE queries to EN for a second BM25 pass
- [ ] Agent API endpoint (`/v1/agent/search`) returning pre-formatted markdown context
- [ ] Selective LLM translation of EN result snippets to DE on demand

## Frontend
- [ ] Date range filter
- [ ] Side-by-side comparison mode (BM25 vs vector vs hybrid)

## Infrastructure
- [ ] Confidential computing deployment
- [ ] Linting + formatting setup (ruff)
- [ ] Custom per-customer page ingestion with traffic-analysis-resistant fetching
