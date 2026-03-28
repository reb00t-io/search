"""Tests for the ingestion pipeline."""

import json
import tempfile
from pathlib import Path

from ingestion.base import Document, SourceAdapter
from ingestion.storage import ContentStore
from ingestion.wikipedia import _chunk_text, _wikitext_to_markdown


class TestDocument:
    def test_document_creation(self):
        doc = Document(
            id="test:1:0",
            source="test",
            title="Test Doc",
            url="https://example.com",
            language="en",
            text="Hello world",
        )
        assert doc.id == "test:1:0"
        assert doc.metadata == {}

    def test_document_with_metadata(self):
        doc = Document(
            id="test:1:0",
            source="test",
            title="Test",
            url="https://example.com",
            language="de",
            text="Hallo Welt",
            metadata={"categories": ["Test"]},
            timestamp="2025-01-01T00:00:00Z",
        )
        assert doc.metadata["categories"] == ["Test"]


class TestContentStore:
    def test_store_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ContentStore(tmp)
            doc = Document(
                id="test:1:0",
                source="test",
                title="Test",
                url="https://example.com",
                language="en",
                text="This is test content for hashing.",
            )
            content_hash = store.store(doc)
            assert len(content_hash) == 64  # SHA-256 hex

            # Read back content
            text = store.read_content(content_hash)
            assert text == "This is test content for hashing."

    def test_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ContentStore(tmp)
            text = "Duplicate content here."
            doc1 = Document(id="a:1:0", source="a", title="A", url="u", language="en", text=text)
            doc2 = Document(id="b:2:0", source="b", title="B", url="u", language="en", text=text)

            h1 = store.store(doc1)
            h2 = store.store(doc2)
            assert h1 == h2  # Same content = same hash

    def test_load_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ContentStore(tmp)
            for i in range(3):
                doc = Document(
                    id=f"test:{i}:0", source="test", title=f"Doc {i}",
                    url="u", language="en", text=f"Content {i}",
                )
                store.store(doc)

            records = store.load_records()
            assert len(records) == 3
            assert records[0]["id"] == "test:0:0"
            assert "content_hash" in records[0]


class TestWikitextToMarkdown:
    def test_headings(self):
        md = _wikitext_to_markdown("== Section ==\nContent here.\n=== Sub ===\nMore.")
        assert "## Section" in md
        assert "### Sub" in md

    def test_links(self):
        md = _wikitext_to_markdown("See [[Albert Einstein]] and [[Physics|physics]].")
        assert "Albert Einstein" in md
        assert "physics" in md
        assert "[[" not in md

    def test_bold_italic(self):
        md = _wikitext_to_markdown("This is '''bold''' and ''italic''.")
        assert "**bold**" in md
        assert "*italic*" in md

    def test_removes_references(self):
        md = _wikitext_to_markdown("Fact.<ref>Source</ref> More text.")
        assert "<ref>" not in md
        assert "More text" in md

    def test_removes_categories(self):
        md = _wikitext_to_markdown("Text.\n[[Category:Science]]\n[[Kategorie:Test]]")
        assert "Category:" not in md
        assert "Kategorie:" not in md


class TestChunking:
    def test_single_chunk(self):
        chunks = _chunk_text("Short text.", "Title", max_tokens=100)
        assert len(chunks) == 1

    def test_multiple_chunks(self):
        text = "## Section 1\n\n" + ("word " * 500) + "\n\n## Section 2\n\n" + ("word " * 500)
        chunks = _chunk_text(text, "Title", max_tokens=200)
        assert len(chunks) >= 2

    def test_preserves_content(self):
        text = "## A\n\nHello world.\n\n## B\n\nGoodbye world."
        chunks = _chunk_text(text, "Title", max_tokens=50)
        combined = " ".join(chunks)
        assert "Hello world" in combined
        assert "Goodbye world" in combined
