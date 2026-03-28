"""Tests for the serving layer."""

from serving.search import _build_filter, _extract_snippet


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
