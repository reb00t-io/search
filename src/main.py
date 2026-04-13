import logging
import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx
from quart import Quart, g, jsonify, redirect, render_template, request, session, url_for

# Add project root to path so pipeline modules can be imported
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from .runtime_logs import configure_runtime_log_capture
    from .streaming import get_session_response, post_chat_response
    from .tool_schemas import DEFAULT_MODE, DEV_MODE, USER_MODE, get_tools_for_mode
except ImportError:
    from runtime_logs import configure_runtime_log_capture
    from streaming import get_session_response, post_chat_response
    from tool_schemas import DEFAULT_MODE, DEV_MODE, USER_MODE, get_tools_for_mode

app = Quart(__name__)
configure_runtime_log_capture()
logger = logging.getLogger(__name__)
REQUEST_LOG_PATH = Path(os.environ.get("REQUEST_LOG_PATH", "data/requests.log"))
REQUEST_LOG_BODY_LIMIT = int(os.environ.get("REQUEST_LOG_BODY_LIMIT", "20000"))
_request_log_lock = threading.Lock()

def _resolve_existing_path(*relative_paths: str) -> Path:
    search_roots = [Path.cwd(), Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent]
    for root in search_roots:
        for relative_path in relative_paths:
            candidate = root / relative_path
            if candidate.exists():
                return candidate
    return Path(relative_paths[0])


def _request_log_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_request_log_text(text: str) -> tuple[str, bool]:
    if len(text) <= REQUEST_LOG_BODY_LIMIT:
        return text, False
    return text[:REQUEST_LOG_BODY_LIMIT], True


def _normalize_request_log_headers(headers) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in {"authorization", "cookie", "set-cookie"}:
            normalized[key] = "[redacted]"
        else:
            normalized[key] = value
    return normalized


def _stringify_request_log_body(body: object) -> str:
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    return str(body)


def _append_request_log(payload: dict[str, object]) -> None:
    REQUEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    with _request_log_lock:
        with REQUEST_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")


def _log_response_chunk(request_id: str, chunk_index: int, chunk: bytes | str) -> None:
    text, truncated = _truncate_request_log_text(_stringify_request_log_body(chunk))
    _append_request_log(
        {
            "ts": _request_log_timestamp(),
            "event": "response_chunk",
            "request_id": request_id,
            "chunk_index": chunk_index,
            "body": text,
            "body_truncated": truncated,
        }
    )


def _is_sse_response(response) -> bool:
    content_type = response.headers.get("Content-Type", "")
    return content_type.startswith("text/event-stream")


class LoggedResponseBody:
    def __init__(self, body, request_id: str):
        self._body = body
        self._request_id = request_id
        self._entered_body = None
        self._chunk_index = 0

    async def __aenter__(self):
        if hasattr(self._body, "__aenter__"):
            self._entered_body = await self._body.__aenter__()
        else:
            self._entered_body = self._body
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _append_request_log(
            {
                "ts": _request_log_timestamp(),
                "event": "response_end",
                "request_id": self._request_id,
                "chunk_count": self._chunk_index,
            }
        )
        if hasattr(self._body, "__aexit__"):
            return await self._body.__aexit__(exc_type, exc, tb)
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        body = self._entered_body if self._entered_body is not None else self._body
        async for chunk in body:
            _log_response_chunk(self._request_id, self._chunk_index, chunk)
            self._chunk_index += 1
            yield chunk


@app.before_request
async def log_client_request() -> None:
    request_id = os.urandom(8).hex()
    g.request_log_id = request_id
    request_body = _stringify_request_log_body(await request.get_data(cache=True, as_text=True))
    body, body_truncated = _truncate_request_log_text(request_body)
    _append_request_log(
        {
            "ts": _request_log_timestamp(),
            "event": "request",
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "query_string": request.query_string.decode("utf-8", errors="replace"),
            "headers": _normalize_request_log_headers(request.headers),
            "body": body,
            "body_truncated": body_truncated,
        }
    )


@app.after_request
async def log_client_response(response):
    request_id = getattr(g, "request_log_id", os.urandom(8).hex())
    response_headers = _normalize_request_log_headers(response.headers)

    if _is_sse_response(response):
        _append_request_log(
            {
                "ts": _request_log_timestamp(),
                "event": "response_start",
                "request_id": request_id,
                "status_code": response.status_code,
                "headers": response_headers,
                "streamed": True,
            }
        )

        response.response = LoggedResponseBody(response.response, request_id)
        return response

    result = response.get_data(as_text=True)
    response_body = (await result) if hasattr(result, "__await__") else result
    body, body_truncated = _truncate_request_log_text(response_body)
    _append_request_log(
        {
            "ts": _request_log_timestamp(),
            "event": "response",
            "request_id": request_id,
            "status_code": response.status_code,
            "headers": response_headers,
            "body": body,
            "body_truncated": body_truncated,
            "streamed": False,
        }
    )
    return response


VERSION_PATH = _resolve_existing_path("VERSION")
PROMPT_PATHS = {
    USER_MODE: _resolve_existing_path("config/user_system_prompt.json"),
    DEV_MODE: _resolve_existing_path("config/dev_system_prompt.json"),
}
DOCS_PATHS = {
    USER_MODE: _resolve_existing_path("docs/user_docs.md"),
    DEV_MODE: _resolve_existing_path("docs/dev_docs.md"),
}

VERSION = VERSION_PATH.read_text().strip()
DEPLOY_DATE = os.environ.get("DEPLOY_DATE", "unknown")

# LLM backend connection
LLM_BASE_URL = os.environ["LLM_BASE_URL"]
LLM_API_KEY  = os.environ.get("LLM_API_KEY", "")
LLM_MODEL    = os.environ.get("LLM_MODEL", "gpt-oss-120b")
STREAM_PACE_SECONDS = float(os.environ.get("STREAM_PACE_SECONDS", "0.003"))

# Client auth — if unset, auth is skipped (useful in local dev)
API_KEY = os.environ.get("API_KEY", "")

# Auth mode: none | password | auth0
AUTH_MODE = os.environ.get("AUTH_MODE", "none")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
if AUTH_MODE == "password" and not AUTH_PASSWORD:
    raise RuntimeError("AUTH_PASSWORD must be set when AUTH_MODE=password")
if AUTH_MODE == "password":
    app.secret_key = hashlib.sha256(AUTH_PASSWORD.encode()).hexdigest()


def _is_authenticated() -> bool:
    if AUTH_MODE == "none":
        return True
    if AUTH_MODE == "password":
        return session.get("authed") is True
    return False  # auth0: not yet implemented

# Session store: session_id -> list[messages]
SESSIONS_PATH = Path(os.environ.get("SESSIONS_PATH", "data/sessions.json"))
sessions: dict[str, list[dict]] = {}
session_modes: dict[str, str] = {}
last_session_id: str | None = None
last_session_ids: dict[str, str] = {}


def _normalize_mode(mode: str | None) -> str:
    if mode in {USER_MODE, DEV_MODE}:
        return mode
    return DEFAULT_MODE


def _load_sessions() -> None:
    global last_session_id
    if SESSIONS_PATH.exists():
        try:
            data = json.loads(SESSIONS_PATH.read_text())
            if "_meta" in data:
                sessions.update(data.get("sessions", {}))
                last_session_id = data["_meta"].get("last_session_id")
                session_modes.update(data["_meta"].get("session_modes", {}))
                last_session_ids.update(data["_meta"].get("last_session_ids", {}))
            else:
                sessions.update(data)  # backwards compat with old format
        except Exception:
            pass


def _save_sessions() -> None:
    SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_PATH.write_text(
        json.dumps(
            {
                "_meta": {
                    "last_session_id": last_session_id,
                    "last_session_ids": last_session_ids,
                    "session_modes": session_modes,
                },
                "sessions": sessions,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _on_session_start(session_id: str, mode: str) -> None:
    global last_session_id
    last_session_id = session_id
    last_session_ids[_normalize_mode(mode)] = session_id
    _save_sessions()


_load_sessions()


def _load_system_prompt(mode: str) -> str:
    normalized_mode = _normalize_mode(mode)
    template = json.loads(PROMPT_PATHS[normalized_mode].read_text())["system_prompt"]
    docs = DOCS_PATHS[normalized_mode].read_text()
    return template.replace("{{docs}}", docs)


@app.route("/login", methods=["GET", "POST"])
async def login():
    if AUTH_MODE == "none":
        return redirect(url_for("index"))
    if _is_authenticated():
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        form = await request.form
        if form.get("password") == AUTH_PASSWORD:
            session["authed"] = True
            return redirect(url_for("index"))
        error = "Incorrect password."
    return await render_template("login.html", error=error)


@app.route("/logout")
async def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
async def index():
    if not _is_authenticated():
        return redirect(url_for("login"))
    return await render_template(
        "index.html",
        version=VERSION,
        deploy_date=DEPLOY_DATE,
        chat_api_key=API_KEY,
    )


@app.route("/chat")
async def chat():
    if not _is_authenticated():
        return redirect(url_for("login"))
    return await render_template(
        "chat.html",
        version=VERSION,
        deploy_date=DEPLOY_DATE,
        chat_api_key=API_KEY,
    )


TODO_PATH = _resolve_existing_path("TODO.md")


@app.route("/todo")
async def todo():
    if not _is_authenticated():
        return redirect(url_for("login"))
    todo_content = TODO_PATH.read_text()
    return await render_template(
        "todo.html",
        version=VERSION,
        deploy_date=DEPLOY_DATE,
        todo_md=todo_content,
    )


@app.route("/v1/pipeline", methods=["GET"])
async def pipeline_api():
    """Return recent pipeline activity from JSONL data files."""
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    limit = min(int(request.args.get("limit", "50")), 200)

    def tail_jsonl(path: Path, n: int) -> list[dict]:
        if not path.exists():
            return []
        lines = path.read_text().strip().splitlines()
        result = []
        for line in lines[-n:]:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return result

    ingested = tail_jsonl(data_dir / "ingested" / "documents.jsonl", limit)
    filtered = tail_jsonl(data_dir / "filtered" / "documents.jsonl", limit)
    rejected = tail_jsonl(data_dir / "rejected" / "rejected.jsonl", limit)

    # Count totals
    def count_lines(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text().strip().splitlines() if line.strip())

    return jsonify({
        "ingested_total": count_lines(data_dir / "ingested" / "documents.jsonl"),
        "filtered_total": count_lines(data_dir / "filtered" / "documents.jsonl"),
        "rejected_total": count_lines(data_dir / "rejected" / "rejected.jsonl"),
        "ingested_recent": ingested,
        "filtered_recent": filtered,
        "rejected_recent": rejected,
    })


@app.route("/logs")
async def logs_page():
    if not _is_authenticated():
        return redirect(url_for("login"))
    return await render_template(
        "logs.html",
        version=VERSION,
        deploy_date=DEPLOY_DATE,
    )


@app.route("/v1/sessions/latest", methods=["GET"])
async def get_latest_session():
    if API_KEY and request.headers.get("Authorization", "") != f"Bearer {API_KEY}":
        return jsonify({"error": "Unauthorized"}), 401
    mode = _normalize_mode(request.args.get("mode"))
    latest_session_id = last_session_ids.get(mode)
    if not latest_session_id and last_session_id in sessions and session_modes.get(last_session_id, DEFAULT_MODE) == mode:
        latest_session_id = last_session_id
    if not latest_session_id or latest_session_id not in sessions:
        return jsonify({"session_id": None, "mode": mode, "messages": []})
    messages = [
        m
        for m in sessions[latest_session_id]
        if m.get("role") in {"user", "assistant"} and m.get("content")
    ]
    return jsonify({"session_id": latest_session_id, "mode": session_modes.get(latest_session_id, mode), "messages": messages})


@app.route("/v1/responses/<session_id>", methods=["GET"])
async def get_session(session_id: str):
    return await get_session_response(
        session_id=session_id,
        sessions=sessions,
        api_key=API_KEY,
        authorization=request.headers.get("Authorization", ""),
    )


@app.route("/v1/responses", methods=["POST"])
async def chat_responses():
    body = await request.get_json(force=True)
    mode = body.get("mode")
    normalized_mode = _normalize_mode(mode)
    if mode is not None and normalized_mode != mode:
        return jsonify({"error": "mode must be 'user' or 'dev'"}), 400

    session_id = body.get("session_id")
    if session_id and session_id in sessions and session_modes.get(session_id, DEFAULT_MODE) != normalized_mode:
        sessions[session_id] = [{"role": "system", "content": _load_system_prompt(normalized_mode)}]
        session_modes[session_id] = normalized_mode
        _save_sessions()

    return await post_chat_response(
        body={**body, "mode": normalized_mode},
        sessions=sessions,
        session_modes=session_modes,
        api_key=API_KEY,
        authorization=request.headers.get("Authorization", ""),
        load_system_prompt=_load_system_prompt,
        save_sessions=_save_sessions,
        on_session_start=_on_session_start,
        tools=get_tools_for_mode(normalized_mode),
        client_factory=httpx.AsyncClient,
        llm_base_url=LLM_BASE_URL,
        llm_api_key=LLM_API_KEY,
        llm_model=LLM_MODEL,
        stream_pace_seconds=STREAM_PACE_SECONDS,
    )


# --- Search API ---
_search_client = None
_search_bm25 = None
_search_initialized = False

def _init_search():
    """Lazily initialize search infrastructure (Qdrant client + BM25 vocab)."""
    global _search_client, _search_bm25, _search_initialized
    if _search_initialized:
        return _search_client is not None

    _search_initialized = True
    try:
        from qdrant_client import QdrantClient
        from indexing.bm25 import BM25Encoder

        qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
        _search_client = QdrantClient(url=qdrant_url, timeout=10)
        # Verify connection
        _search_client.get_collections()

        _search_bm25 = BM25Encoder()
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
        vocab_path = data_dir / "index" / "bm25_vocab.json"
        _search_bm25.load(vocab_path)
        logger.info("Search initialized: Qdrant at %s, vocab=%d tokens", qdrant_url, len(_search_bm25.vocab))
        return True
    except Exception as e:
        logger.warning("Search not available: %s", e)
        _search_client = None
        _search_bm25 = None
        return False


@app.route("/v1/search", methods=["GET"])
async def search_endpoint():
    if not _init_search():
        return jsonify({"error": "Search not available. Run indexing pipeline first."}), 503

    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    lang = request.args.get("lang", "all")
    source = request.args.get("source", "all")
    mode = request.args.get("mode", "hybrid")
    group_by = request.args.get("group_by", "docs")
    limit = min(int(request.args.get("limit", "10")), 100)
    offset = int(request.args.get("offset", "0"))

    if mode not in ("hybrid", "bm25", "vector"):
        return jsonify({"error": f"Invalid mode: {mode}. Use: hybrid, bm25, vector"}), 400
    if group_by not in ("docs", "chunks"):
        return jsonify({"error": f"Invalid group_by: {group_by}. Use: docs, chunks"}), 400

    from serving.search import search
    result = search(
        client=_search_client,
        bm25=_search_bm25,
        query=q,
        mode=mode,
        lang=lang if lang != "all" else None,
        source=source if source != "all" else None,
        limit=limit,
        offset=offset,
        group_by=group_by,
    )
    return jsonify(result)


def _read_ingestion_schedule(data_dir: Path) -> dict | None:
    """Read the active ingestion schedule written by the ingestion service on startup."""
    schedule_path = data_dir / "ingestion_schedule.json"
    if not schedule_path.exists():
        return None
    try:
        return json.loads(schedule_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _get_index_stats():
    """Collect current index statistics."""
    from collections import Counter
    from indexing.indexer import COLLECTION_NAME

    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    stats = {}

    # Qdrant collection info
    try:
        info = _search_client.get_collection(COLLECTION_NAME)
        stats["indexed_points"] = info.points_count or 0
        stats["segments"] = info.segments_count or 0
    except Exception:
        stats["indexed_points"] = 0
        stats["segments"] = 0

    # Count documents from filtered JSONL
    filtered_path = data_dir / "filtered" / "documents.jsonl"
    source_counts: Counter = Counter()
    ct_counts: Counter = Counter()
    if filtered_path.exists():
        for line in filtered_path.read_text().strip().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                source_counts[rec.get("source", "?")] += 1
                ct_counts[rec.get("content_type", "full_text")] += 1
            except json.JSONDecodeError:
                pass

    stats["documents"] = sum(source_counts.values())
    stats["full_text"] = ct_counts.get("full_text", 0)
    stats["abstracts"] = ct_counts.get("abstract", 0)
    stats["by_source"] = dict(sorted(source_counts.items()))

    # Sizes
    def _dir_size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0

    stats["content_bytes"] = _dir_size(data_dir / "content")
    stats["data_bytes"] = _dir_size(data_dir)

    # Active ingestion schedule (written by ingestion service on startup)
    stats["schedule"] = _read_ingestion_schedule(data_dir)

    # Last ingestion cycle log (written by ingestion after each cycle)
    ingestion_log_path = data_dir / "stats" / "last_ingestion.json"
    if ingestion_log_path.exists():
        try:
            stats["last_ingestion"] = json.loads(ingestion_log_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    return stats


@app.route("/v1/stats", methods=["GET"])
async def stats_api():
    if not _init_search():
        return jsonify({"error": "Search not available"}), 503
    return jsonify(_get_index_stats())


@app.route("/stats")
async def stats_page():
    if not _is_authenticated():
        return redirect(url_for("login"))

    # Load history
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    history_path = data_dir / "stats" / "history.jsonl"
    history = []
    if history_path.exists():
        for line in history_path.read_text().strip().splitlines():
            if not line.strip():
                continue
            try:
                history.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    return await render_template(
        "stats.html",
        version=VERSION,
        deploy_date=DEPLOY_DATE,
        history_json=json.dumps(history),
    )


if __name__ == "__main__":
    import atexit
    import uvicorn

    PID_FILE = Path(os.environ.get("PID_FILE", "data/search.pid"))
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))

    logger.info("search v%s (deployed %s, pid %d)", VERSION, DEPLOY_DATE, os.getpid())
    port = int(os.environ["PORT"])
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
