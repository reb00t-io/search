# AGENTS.md

## 1. Mission & Priorities
**Role of the agent in this repository:**
- Build and maintain a private search engine pipeline (ingest → filter → index → serve) for agent-safe web search

**Decision priority order:**
- correctness > security > maintainability > performance > speed

**Global constraints or goals:**
- All content must come from trusted sources only (Wikipedia for MVP)
- Content must be filtered for prompt injection patterns before indexing
- System must be deployable in confidential computing environments (future)
- German is the primary language; English is secondary

## 2. Executable Commands (Ground Truth)
All commands listed here must work.

- Install / setup:
  - `direnv allow` (creates venv, installs deps, sets env vars)
- Dev server:
  - `python src/main.py`
- Lint:
  - N/A (to be added)
- Format:
  - N/A (to be added)
- Type check:
  - N/A (to be added)
- Unit tests:
  - `pytest test/`
- Integration / e2e tests:
  - `./test/e2e.sh`

## 3. Repository Map
**High-level structure:**
- `src/` — Quart web application (API server, streaming, tools)
- `config/` — System prompts, nginx config
- `docs/` — Specification (spec.md), user/dev documentation
- `scripts/` — Build, deploy, venv scripts
- `test/` — Unit tests (pytest) and e2e tests
- `data/` — Runtime data: ingested content, indices, sessions (gitignored)

**Entry points:**
- Backend: `src/main.py`
- Frontend: `src/templates/index.html` + `src/static/chat/chat.js`
- CLI / Worker / Service: N/A (to be added for pipeline workers)

**Key configuration locations:**
- `.envrc` — Environment variables (PORT, LLM config, AUTH_MODE)
- `docker-compose.yml` — Service definitions (app + Qdrant)
- `config/` — AI assistant system prompts

## 4. Definition of Done
For any change, the following must hold:
- [ ] Unit tests pass (`pytest test/`)
- [ ] E2e test passes (`./test/e2e.sh`)
- [ ] No new security issues (especially around content injection)
- [ ] spec.md updated if pipeline behavior changes

## 5. Code Style & Conventions (Repo-Specific)
- Language(s) + version(s):
  - `Python@3.13`
- Formatter:
  - N/A (to be configured)
- Naming conventions:
  - snake_case for Python functions/variables
  - Document IDs: `{source}:{lang}:{id}:{chunk_index}` (e.g., `wiki:de:12345:0`)
- Error handling pattern:
  - Log and continue for non-critical pipeline errors (bad article, parse failure)
  - Fail fast for infrastructure errors (DB connection, missing config)
- Logging rules:
  - Log pipeline progress (articles processed, errors, timings)
  - Never log full document content at INFO level

## 6. Boundaries & Guardrails
The agent must **not**:
- Index content from untrusted or unvetted sources
- Expose raw document content without safety filtering
- Make external API calls from the serving path (except to local Qdrant/model server)

When unsure:
- Prefer the smallest possible change
- Leave a TODO with context rather than guessing

## 7. Security & Privacy Constraints
- Sensitive data locations:
  - `data/` — Contains all ingested and indexed content
  - `.envrc` — Contains API keys and credentials
- Redaction / handling rules:
  - Never log API keys or auth tokens
  - Filter content for prompt injection patterns before indexing
- Threat model notes:
  - Primary threats: prompt injection via indexed content, prompt leakage via query exfiltration
  - Mitigation: trusted sources only, content filtering, CC deployment (future)

## 8. Common Pitfalls & Couplings
- Wikipedia dump format changes occasionally — pin `mwparserfromhell` version and test against sample dumps
- Qdrant collection schema changes require re-indexing — version the collection name
- Embedding model changes require full re-embedding — version the vector collection
- Qdrant is the single search backend (dense vectors + native BM25 sparse vectors) — no separate BM25 engine

## 9. Examples & Canonical Patterns (Optional)
N/A — to be added after implementation

## 10. Pull Requests & Branching
Default branch: main

When a PR is requested, create a branch agent/<branch_name> and create a PR from there using gh
