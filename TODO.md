# TODO

## Source Coverage

Current sources: Wikipedia (DE+EN), arXiv, gesetze-im-internet.de, rechtsprechung-im-internet.de, PubMed, RKI, Tagesschau, Deutsche Welle

Sources to add for reasonable coverage:

### News
- [x] **Tagesschau.de** — ARD's public news, RSS feeds available, German-language
- [x] **DW (Deutsche Welle)** — German + English, RSS + API, international perspective
- [ ] **Wikinews** (DE+EN) — free content, same MediaWiki API as Wikipedia

### Health
- [ ] **WHO fact sheets** — structured HTML, ~400 topics, authoritative
- [x] **RKI (Robert Koch Institut)** — German public health, publications + reports
- [ ] **Gesundheitsinformation.de** (IQWiG) — evidence-based health info in German

### Law / Government
See `docs/german-law-sources.md` for the full German tax/law source plan and status.
- [x] **Rechtsprechung im Internet** — federal court decisions (BVerfG, BGH, BVerwG, BFH, …), BFH prioritized
- [ ] **BMF-Schreiben** — tax administration guidance; PDF extraction pipeline needed
- [ ] **BMF Amtliche Handbücher** — official tax handbooks (Richtlinien/Hinweise); needs crawler
- [ ] **Bundestag DIP** — legislative materials; REST API, requires API key
- [ ] **EUR-Lex** — EU legislation, bulk download available, DE+EN (start with curated CELEX list)
- [ ] **Bundesgesetzblatt / recht.bund.de** — official gazette, change tracking; PDF-first
- [ ] **NeuRIS** — watch: future open-data API for laws + case law, may replace juris XML scraping
- [ ] **Verwaltungsvorschriften** — administrative regulations
- [ ] **Landesrecht portals** — state law, fragmented/partially fee-based

### Coding / Tech
- [ ] **Stack Overflow data dump** — quarterly CC-licensed dump, Q&A format
- [ ] **Python docs / MDN Web Docs** — structured, high quality, agents query these often
- [ ] **Linux man pages** — available as structured text
- [ ] **Arch Wiki** — high-quality Linux/sysadmin docs, CC-licensed, MediaWiki API

### Reference / General
- [ ] **Wiktionary (DE+EN)** — definitions, translations, same MediaWiki API
- [ ] **Simple English Wikipedia** — concise factual answers
- [ ] **OpenStreetMap wiki** — geographic/place data

## Ingestion

- [ ] arXiv: full-text extraction (currently abstracts only). Use ar5iv HTML versions where available, fall back to LaTeX/pandoc conversion.
- [ ] Wikipedia: switch from API to dump-based ingestion for full corpus
- [ ] Wikipedia: real-time updates via Wikimedia EventStreams SSE
- [ ] arXiv: incremental updates via OAI-PMH or RSS polling
- [ ] Content GC: periodic cleanup of orphaned content-hash files
- [ ] **Novelty-condensed document representation** — During input processing, each document should be transformed into an additional condensed text that retains only the information the document genuinely adds to existing knowledge. Everything that is already well-known or redundant across the corpus is removed, leaving a compact "novelty-only" representation. This condensed form is stored alongside the original text and can be used for highly efficient search queries that surface what is truly new or unique in a document. Implementation must be done very carefully: aggressive removal risks losing subtle but important details, while too little removal defeats the purpose. A conservative, iterative approach with quality checks is essential.

## Filtering

- [ ] Near-duplicate detection (simhash/minhash) across sources
- [ ] Anomaly scoring for unusual token distributions

## Indexing

- [ ] Zero-downtime reindex (create new collection, swap alias, delete old)
- [ ] Evaluate SPLADE++ as an alternative to BM25 sparse vectors

## Ranking

- [ ] **Authority-based ranking (PageRank-style)** — Introduce a static authority score for documents based on their link structure and citation graph, similar to PageRank. Documents referenced by many other high-quality documents should rank higher. This is especially relevant for Wikipedia (internal links) and arXiv/PubMed (citation networks).
- [ ] **Scientific review and citation scores** — For academic sources (arXiv, PubMed), incorporate peer review signals and citation counts into ranking. Papers with more citations, published in higher-impact venues, or with formal peer review should receive a ranking boost over preprints or less-cited work.
- [ ] **Document quality and novelty assessment** — Score each document on writing quality (coherence, completeness, factual density) and novelty (how much unique information it contributes vs. what is already in the index). Use these scores as ranking signals so that high-quality, information-dense documents surface above shallow or redundant content.

## Serving

- [x] RAG context injection in chat (semantic search, top-5 deduplicated chunks with source links prepended as system message)
- [ ] RAG: use hybrid retrieval + reranking instead of vector-only once cross-encoder lands
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
