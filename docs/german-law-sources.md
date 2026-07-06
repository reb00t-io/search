# German Law & Tax Research Assistant — Plan and Source Inventory

## Goal

Extend the search engine into the backend of a **general-purpose German AI
assistant that is especially strong at tax- and law-related research**. The
strategy: ingest official primary sources (laws, court decisions,
administrative guidance) into the trusted local index, so the agent can ground
its answers with citations — via active search tools *and* automatic RAG
context injection (see `spec.md`, Stage 4).

All targeted content is official German government material. German laws,
regulations, official decrees, court decisions and official headnotes are not
protected by copyright (**§ 5 UrhG**), which makes them safe to ingest and
serve. Website/API terms are still checked per source.

## Ingestion priority (for a tax/law assistant)

1. Gesetze im Internet — federal statutes (NeuRIS as future replacement)
2. BMF Amtliche Handbücher — curated official tax handbooks
3. BMF-Schreiben — tax administration guidance
4. BFH decisions — tax case law
5. Rechtsprechung im Internet — other federal courts (BVerfG, BGH, BVerwG, …)
6. Bundesgesetzblatt (recht.bund.de) — change tracking of promulgated laws
7. Bundestag DIP — legislative materials (bills, Drucksachen, protocols)
8. EUR-Lex / CURIA — EU law and case law (VAT, GDPR, AI Act, …)
9. Landesrecht portals — state law

High-value statutes ingested first (implemented via `PRIORITY_LAWS` in
`ingestion/gesetze.py`): HGB, GmbHG, AktG, UmwG, AO, EStG, KStG, GewStG, UStG,
GrEStG, ErbStG, BewG, FGO, StBerG, BGB, ZPO, InsO, BDSG.

## Source inventory and integration status

| Source | Content | Status | Notes |
|---|---|---|---|
| **Gesetze im Internet** (gesetze-im-internet.de) | All federal statutes and regulations | ✅ **Integrated** (`ingestion/gesetze.py`) | juris TOC XML (`gii-toc.xml`) + one XML zip per law. High-value statutes now prioritized via `PRIORITY_LAWS`. |
| **Rechtsprechung im Internet** (rechtsprechung-im-internet.de) | Federal case law since ~2010: BVerfG, BGH, BVerwG, **BFH**, BAG, BSG, BPatG | ✅ **Integrated** (`ingestion/rechtsprechung.py`) | Same juris infrastructure: `rii-toc.xml` + one XML zip per decision (Leitsatz, Tenor, Tatbestand, Gründe). BFH (tax) decisions ingested first, then newest-first. Covers plan items 4 *and* 5. |
| **BFH V/NV decision search** (bundesfinanzhof.de) | Tax case law, weekly updates | ⚠️ Partially covered | BFH decisions arrive via Rechtsprechung im Internet. The BFH website has no stable machine-readable bulk interface; revisit for freshness (weekly RSS scraping) if the rii feed lags. |
| **BMF-Schreiben** (bundesfinanzministerium.de) | Tax administration guidance | ❌ Not integrated | Published as HTML listing + **PDF** documents; no machine-readable feed found (probed RSS endpoints return 404). Needs a PDF-extraction pipeline (e.g. `pypdf`/`pdfplumber`) + listing scraper. Highest-value missing source for practical tax questions. |
| **BMF Amtliche Handbücher** (bmf-esth.de etc.) | Official tax handbooks (ESt, KSt, GewSt, AO, LSt, …) by assessment year | ❌ Not integrated | Online handbooks are HTML but deeply nested per-paragraph navigation without a bulk export; needs a dedicated crawler per handbook. Much of the primary content (statutes) is already covered via Gesetze im Internet; the added value is Richtlinien/Hinweise. |
| **Bundesgesetzblatt** (recht.bund.de) | Legally binding promulgated laws, change tracking | ❌ Not integrated | Open data access exists but documents are PDF-first; value is *change tracking*, not corpus content (consolidated texts already come from Gesetze im Internet). Revisit together with NeuRIS. |
| **NeuRIS** (neuris.bund.de) | Successor platform: laws + case law as open data with APIs | ❌ Not integrated (watch) | Still being rolled out; once its API covers consolidated federal law it can replace the juris XML scraping. |
| **Bundestag DIP** (dip.bundestag.de) | Bills, Drucksachen, plenary protocols | ❌ Not integrated | Documented REST API (JSON + full text), but **requires an API key** (personal key via dip.bundestag.de registration). Adapter is straightforward once a key is configured (`DIP_API_KEY`). |
| **EUR-Lex** (eur-lex.europa.eu) | EU regulations/directives, consolidated texts, OJ | ❌ Not integrated | Bulk access via Cellar SPARQL/REST; web-service account needed for the search API. Large scope — needs curation (VAT directive, GDPR, AI Act, company law) rather than full-corpus ingestion. |
| **CURIA** (curia.europa.eu) | EU case law | ❌ Not integrated | No official bulk API; EU case law is also on EUR-Lex (CELEX 6xxxx). Prefer EUR-Lex route. |
| **Landesrecht portals** (Justizportal, per-state) | State law and state case law | ❌ Not integrated | Fragmented per state, partially fee-based (juris-operated). Low priority for the MVP; revisit per concrete user need (e.g. Berlin/Bavaria building or school law). |
| **esteuer.de / E-Bilanz taxonomies** | XBRL taxonomies + Excel visualizations | ❌ Not integrated | Machine-readable but XBRL schema data, not prose — poor fit for a text search index. Better served later as a dedicated tool/lookup. |

## How the assistant uses these sources

- **Active search:** the agent's `web_search` tool queries the local hybrid
  index (`/v1/search`), which now includes statutes and federal case law.
- **RAG:** every chat prompt triggers a semantic search; the top 5
  deduplicated chunks are prepended to the conversation as a system message
  with source links (see `serving/rag.py`). The agent cites sources as
  markdown links.
- **Trust model:** only official government sources are ingested (AGENTS.md
  boundary: trusted sources only); all content still passes the injection
  filters in `filtering/` before indexing.

## Next steps (ordered by value)

1. **BMF-Schreiben PDF pipeline** — biggest practical-tax gap; needs PDF text
   extraction plus a listing scraper.
2. **DIP adapter** — register for an API key, then a thin JSON adapter.
3. **EUR-Lex curated ingestion** — start with a handpicked CELEX list (VAT
   directive, GDPR, AI Act) via the public REST interface.
4. **BMF Amtliche Handbücher crawler** — Richtlinien/Hinweise text.
5. **NeuRIS migration** — replace juris XML scraping when the API is stable.
