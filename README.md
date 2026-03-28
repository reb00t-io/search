# Search

A private search engine for AI agents, designed to serve high-quality, trusted content while preventing prompt leakage and injection. Built for deployment in confidential computing (CC) environments.

## Architecture

Continuous pipeline: **Ingest** → **Filter** → **Index** → **Serve**

- **Ingestion:** Wikipedia dumps (DE + EN) with real-time updates via Wikimedia EventStreams
- **Filtering:** Quality checks (stub removal, disambiguation filtering) and safety checks (prompt injection pattern detection)
- **Indexing:** Hybrid — BM25 (native sparse vectors) + dense vector search, both in Qdrant
- **Serving:** Hybrid retrieval with cross-encoder reranking, exposed as REST API + agent-optimized endpoint
- **Frontend:** Web UI for search testing and quality evaluation

Language focus: German (primary) and English, using multilingual embeddings (`multilingual-e5`) for cross-language retrieval without blanket translation.

**Confidential computing:** The system is designed to run entirely within a CC enclave (out of scope for now). No query data leaves the enclave; only source data downloads are external.

See [docs/spec.md](docs/spec.md) for the full specification.

## Quick Start

```bash
# Allow direnv to load the environment
direnv allow

# Run locally
python src/main.py
# → http://localhost:$PORT

# Or run with Docker
./scripts/build.sh
docker compose up
```

## Project Structure

```
src/                     # Application code (Quart web server)
config/                  # System prompts, nginx config
docs/
  spec.md               # Search engine specification
  user_docs.md          # User-facing documentation
  dev_docs.md           # Developer documentation
scripts/                # Build, deploy, venv setup
test/                   # Unit + e2e tests
data/                   # Runtime data (indices, ingested content)
.github/workflows/      # CI pipeline
```
