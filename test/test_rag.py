"""Tests for the RAG context layer (serving/rag.py)."""

from unittest.mock import MagicMock

from serving.rag import RAG_TOP_K, dedupe_chunks, format_rag_context


def _point(doc_id, score, text="chunk text", title="Title", url="https://example.com/x",
           content_hash=None, source="wiki"):
    point = MagicMock()
    point.score = score
    point.payload = {
        "doc_id": doc_id,
        "title": title,
        "url": url,
        "source": source,
        "text": text,
        "content_hash": content_hash or f"hash-{doc_id}",
    }
    return point


class TestDedupeChunks:
    def test_caps_at_top_k(self):
        points = [_point(f"wiki:de:{i}:0", 1.0 - i * 0.01, text=f"text {i}") for i in range(20)]
        chunks = dedupe_chunks(points)
        assert len(chunks) == RAG_TOP_K

    def test_sorted_by_score_desc(self):
        points = [_point("a:1:0", 0.2, text="low"), _point("b:1:0", 0.9, text="high")]
        chunks = dedupe_chunks(points)
        assert chunks[0]["text"] == "high"
        assert chunks[1]["text"] == "low"

    def test_dedupes_identical_content(self):
        # Same content hash under different doc IDs (e.g. re-ingested document)
        points = [
            _point("wiki:de:1:0", 0.9, content_hash="same"),
            _point("wiki:de:1-copy:0", 0.8, content_hash="same"),
            _point("wiki:de:2:0", 0.7, content_hash="other"),
        ]
        chunks = dedupe_chunks(points)
        assert len(chunks) == 2
        assert {c["doc_id"] for c in chunks} == {"wiki:de:1:0", "wiki:de:2:0"}

    def test_dedupes_same_chunk_id(self):
        points = [
            _point("wiki:de:1:0", 0.9, content_hash="h1"),
            _point("wiki:de:1:0", 0.8, content_hash="h2"),
        ]
        chunks = dedupe_chunks(points)
        assert len(chunks) == 1

    def test_skips_empty_text(self):
        points = [_point("a:1:0", 0.9, text="  "), _point("b:1:0", 0.5, text="real")]
        chunks = dedupe_chunks(points)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "real"

    def test_truncates_long_chunks(self):
        points = [_point("a:1:0", 0.9, text="x" * 5000)]
        chunks = dedupe_chunks(points)
        assert len(chunks[0]["text"]) == 1500


class TestFormatRagContext:
    def test_empty_returns_none(self):
        assert format_rag_context([]) is None

    def test_includes_source_links_and_text(self):
        chunks = dedupe_chunks([
            _point("gesetze:estg:0", 0.9, text="§ 1 Steuerpflicht ...",
                   title="Einkommensteuergesetz", url="https://www.gesetze-im-internet.de/estg/",
                   source="gesetze"),
        ])
        context = format_rag_context(chunks)
        assert context is not None
        assert "Einkommensteuergesetz" in context
        assert "https://www.gesetze-im-internet.de/estg/" in context
        assert "§ 1 Steuerpflicht" in context
        assert "(gesetze)" in context
        # Instructional header present
        assert "reference data" in context
        # Doc ID reference for full-text retrieval via /v1/doc
        assert "ID: gesetze:estg:0" in context
        assert "/v1/doc?id=" in context

    def test_numbers_results(self):
        chunks = dedupe_chunks([
            _point("a:1:0", 0.9, text="one"),
            _point("b:1:0", 0.8, text="two"),
        ])
        context = format_rag_context(chunks)
        assert "### Result 1:" in context
        assert "### Result 2:" in context
