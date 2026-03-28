"""Tests for the shared chunking utility."""

from ingestion.chunking import chunk_text


class TestChunkText:
    def test_short_text_single_chunk(self):
        chunks = chunk_text("Short text here.", title="Title")
        assert len(chunks) == 1
        assert "# Title" in chunks[0]

    def test_splits_at_headings(self):
        text = "## A\n\n" + ("word " * 500) + "\n\n## B\n\n" + ("word " * 500)
        chunks = chunk_text(text, title="Doc", target_words=200)
        assert len(chunks) >= 2

    def test_splits_large_section_at_paragraphs(self):
        # One huge section with no headings but multiple paragraphs
        text = "\n\n".join(f"Paragraph {i}. " + ("word " * 200) for i in range(10))
        chunks = chunk_text(text, target_words=300)
        assert len(chunks) >= 3

    def test_splits_huge_paragraph_at_sentences(self):
        # One massive paragraph
        text = ". ".join(f"Sentence number {i} with some content" for i in range(500))
        chunks = chunk_text(text, target_words=200)
        assert len(chunks) >= 3
        # No chunk should exceed hard max
        for chunk in chunks:
            assert len(chunk.split()) <= 1600  # some slack over MAX_CHUNK_WORDS

    def test_hard_max_enforced(self):
        # Text with no natural break points
        text = "longword " * 3000
        chunks = chunk_text(text, target_words=200)
        for chunk in chunks:
            assert len(chunk.split()) <= 1600

    def test_preserves_content(self):
        text = "## Section A\n\nHello world.\n\n## Section B\n\nGoodbye world."
        chunks = chunk_text(text, title="Test", target_words=50)
        combined = " ".join(chunks)
        assert "Hello world" in combined
        assert "Goodbye world" in combined

    def test_empty_text(self):
        chunks = chunk_text("", title="Empty")
        # Should return something (at least the title)
        assert len(chunks) >= 1
