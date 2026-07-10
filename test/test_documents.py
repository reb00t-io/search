"""Tests for full-document lookup (serving/documents.py)."""

import json

from ingestion.base import Document
from ingestion.storage import ContentStore
from serving.documents import DocumentLookup, fetch_document


def _store_filtered(data_dir, docs: list[Document]) -> None:
    """Store documents and register them in filtered/documents.jsonl."""
    store = ContentStore(data_dir)
    filtered_dir = data_dir / "filtered"
    filtered_dir.mkdir(parents=True, exist_ok=True)
    with open(filtered_dir / "documents.jsonl", "a", encoding="utf-8") as f:
        for doc in docs:
            content_hash = store.store(doc)
            record = {
                "id": doc.id,
                "source": doc.source,
                "title": doc.title,
                "url": doc.url,
                "language": doc.language,
                "content_hash": content_hash,
                "timestamp": doc.timestamp,
            }
            f.write(json.dumps(record) + "\n")


def _doc(doc_id: str, text: str, title: str = "Testgesetz") -> Document:
    return Document(
        id=doc_id, source="gesetze", title=title,
        url="https://www.gesetze-im-internet.de/testg/",
        language="de", text=text, timestamp="2026-01-01",
    )


class TestDocumentLookup:
    def test_exact_chunk_id(self, tmp_path):
        _store_filtered(tmp_path, [_doc("gesetze:testg:0", "§ 1 Erster Teil."),
                                   _doc("gesetze:testg:1", "§ 2 Zweiter Teil.")])
        lookup = DocumentLookup(tmp_path)
        result = fetch_document(lookup, "gesetze:testg:1", max_chars=1000)
        assert result is not None
        assert result["text"] == "§ 2 Zweiter Teil."
        assert result["chunks"] == 1
        assert result["title"] == "Testgesetz"
        assert result["truncated"] is False

    def test_base_id_concatenates_chunks_in_order(self, tmp_path):
        # Register out of order — output must follow chunk index order
        _store_filtered(tmp_path, [_doc("gesetze:testg:2", "drei"),
                                   _doc("gesetze:testg:0", "eins"),
                                   _doc("gesetze:testg:1", "zwei")])
        lookup = DocumentLookup(tmp_path)
        result = fetch_document(lookup, "gesetze:testg", max_chars=1000)
        assert result["text"] == "eins\n\nzwei\n\ndrei"
        assert result["chunks"] == 3

    def test_unknown_id_returns_none(self, tmp_path):
        _store_filtered(tmp_path, [_doc("gesetze:testg:0", "text")])
        lookup = DocumentLookup(tmp_path)
        assert fetch_document(lookup, "gesetze:anderes:0", max_chars=1000) is None

    def test_truncation(self, tmp_path):
        _store_filtered(tmp_path, [_doc("gesetze:testg:0", "x" * 500)])
        lookup = DocumentLookup(tmp_path)
        result = fetch_document(lookup, "gesetze:testg:0", max_chars=100)
        assert len(result["text"]) == 100
        assert result["truncated"] is True

    def test_reloads_when_file_grows(self, tmp_path):
        _store_filtered(tmp_path, [_doc("gesetze:a:0", "erstes")])
        lookup = DocumentLookup(tmp_path)
        assert fetch_document(lookup, "gesetze:a:0", max_chars=100) is not None
        assert fetch_document(lookup, "gesetze:b:0", max_chars=100) is None

        _store_filtered(tmp_path, [_doc("gesetze:b:0", "zweites")])
        result = fetch_document(lookup, "gesetze:b:0", max_chars=100)
        assert result is not None
        assert result["text"] == "zweites"

    def test_missing_data_dir(self, tmp_path):
        lookup = DocumentLookup(tmp_path / "nonexistent")
        assert fetch_document(lookup, "gesetze:testg:0", max_chars=100) is None

    def test_reingested_document_uses_latest_record(self, tmp_path):
        _store_filtered(tmp_path, [_doc("gesetze:testg:0", "alte Fassung")])
        _store_filtered(tmp_path, [_doc("gesetze:testg:0", "neue Fassung")])
        lookup = DocumentLookup(tmp_path)
        result = fetch_document(lookup, "gesetze:testg:0", max_chars=1000)
        assert result["text"] == "neue Fassung"
        assert result["chunks"] == 1


class TestSectionStyleIds:
    """§-style doc IDs ("gesetze:hgb:267a") resolve via metadata.sections."""

    def _lookup(self, tmp_path):
        import json
        from serving.documents import DocumentLookup

        records = [
            {"id": "gesetze:hgb:0", "source": "gesetze", "title": "HGB",
             "content_hash": "h0", "metadata": {"sections": ["§ 266", "§ 267"]}},
            {"id": "gesetze:hgb:1", "source": "gesetze", "title": "HGB",
             "content_hash": "h1", "metadata": {"sections": ["§ 267a", "§ 268"]}},
        ]
        filtered = tmp_path / "filtered"
        filtered.mkdir(parents=True, exist_ok=True)
        (filtered / "documents.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records) + "\n", "utf-8")
        return DocumentLookup(tmp_path)

    def test_letter_suffix_section_resolves(self, tmp_path):
        lookup = self._lookup(tmp_path)
        records = lookup.get_records("gesetze:hgb:267a")
        assert len(records) == 1 and records[0]["id"] == "gesetze:hgb:1"

    def test_paragraph_sign_prefix_resolves(self, tmp_path):
        lookup = self._lookup(tmp_path)
        records = lookup.get_records("gesetze:hgb:§268")
        assert len(records) == 1 and records[0]["id"] == "gesetze:hgb:1"

    def test_numeric_tail_stays_chunk_index(self, tmp_path):
        lookup = self._lookup(tmp_path)
        records = lookup.get_records("gesetze:hgb:1")
        assert len(records) == 1 and records[0]["id"] == "gesetze:hgb:1"
        # chunk 267 does not exist and must NOT resolve as § 267
        assert lookup.get_records("gesetze:hgb:267") == []

    def test_unknown_section_returns_empty(self, tmp_path):
        lookup = self._lookup(tmp_path)
        assert lookup.get_records("gesetze:hgb:999z") == []
