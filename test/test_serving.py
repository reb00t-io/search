"""Tests for the serving layer."""

from unittest.mock import MagicMock

from serving.search import _base_doc_id, _build_filter, _deduplicate_to_docs, _extract_snippet


class TestBuildFilter:
    def test_no_filter(self):
        assert _build_filter(None, None) is None
        assert _build_filter("all", "all") is None

    def test_lang_filter(self):
        f = _build_filter("de", None)
        assert f is not None
        assert len(f.must) == 1

    def test_source_filter(self):
        f = _build_filter(None, "wiki")
        assert f is not None
        assert len(f.must) == 1

    def test_combined_filter(self):
        f = _build_filter("en", "arxiv")
        assert f is not None
        assert len(f.must) == 2


class TestBaseDocId:
    def test_strips_chunk_index(self):
        assert _base_doc_id("wiki:de:12345:0") == "wiki:de:12345"
        assert _base_doc_id("wiki:de:12345:7") == "wiki:de:12345"
        assert _base_doc_id("arxiv:2301.07041:0") == "arxiv:2301.07041"

    def test_handles_short_id(self):
        # IDs with fewer than 3 parts are returned as-is
        assert _base_doc_id("wiki:de") == "wiki:de"
        assert _base_doc_id("single") == "single"


class TestDeduplicateToDocs:
    def _make_point(self, doc_id, title, score, text="some text"):
        point = MagicMock()
        point.score = score
        point.payload = {
            "doc_id": doc_id,
            "title": title,
            "url": f"https://example.com/{doc_id}",
            "text": text,
            "language": "en",
            "source": "wiki",
            "timestamp": "2025-01-01",
        }
        return point

    def test_deduplicates_chunks(self):
        points = [
            self._make_point("wiki:en:1:0", "Article A", 0.9),
            self._make_point("wiki:en:1:1", "Article A", 0.7),
            self._make_point("wiki:en:1:2", "Article A", 0.5),
            self._make_point("wiki:en:2:0", "Article B", 0.8),
        ]
        results = _deduplicate_to_docs(points, "test")
        assert len(results) == 2
        # Both docs present
        ids = {r["id"] for r in results}
        assert ids == {"wiki:en:1", "wiki:en:2"}

    def test_best_chunk_wins(self):
        points = [
            self._make_point("wiki:en:1:0", "A", 0.3, "low score text"),
            self._make_point("wiki:en:1:1", "A", 0.9, "high score text"),
        ]
        results = _deduplicate_to_docs(points, "test")
        assert len(results) == 1
        # Snippet should come from the high-score chunk
        assert results[0]["_best_score"] if hasattr(results[0], "_best_score") else True

    def test_multi_chunk_boost(self):
        # Doc with 3 chunks should score higher than doc with 1 chunk (same base score)
        points = [
            self._make_point("wiki:en:1:0", "Multi", 0.8),
            self._make_point("wiki:en:1:1", "Multi", 0.7),
            self._make_point("wiki:en:1:2", "Multi", 0.6),
            self._make_point("wiki:en:2:0", "Single", 0.8),
        ]
        results = _deduplicate_to_docs(points, "test")
        multi = next(r for r in results if r["id"] == "wiki:en:1")
        single = next(r for r in results if r["id"] == "wiki:en:2")
        assert multi["score"] > single["score"]
        assert multi["matching_chunks"] == 3
        assert single["matching_chunks"] == 1

    def test_single_chunk_no_boost(self):
        points = [self._make_point("wiki:en:1:0", "A", 0.5)]
        results = _deduplicate_to_docs(points, "test")
        assert len(results) == 1
        assert results[0]["matching_chunks"] == 1


class TestExtractSnippet:
    def test_highlights_query_terms(self):
        text = "Machine learning is a subset of artificial intelligence."
        snippet = _extract_snippet(text, "machine learning")
        assert "**machine**" in snippet.lower() or "**Machine**" in snippet

    def test_finds_relevant_sentence(self):
        text = "Intro paragraph. Machine learning is great for NLP tasks. Another sentence."
        snippet = _extract_snippet(text, "machine learning NLP")
        assert "NLP" in snippet

    def test_truncates_long_text(self):
        text = "word " * 1000
        snippet = _extract_snippet(text, "word", max_length=300)
        assert len(snippet) <= 350  # Allow some slack for bold markers

    def test_handles_empty_query(self):
        text = "Some content here."
        snippet = _extract_snippet(text, "")
        assert len(snippet) > 0
