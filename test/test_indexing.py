"""Tests for the indexing pipeline."""

from indexing.bm25 import BM25Encoder, tokenize


class TestTokenize:
    def test_basic(self):
        tokens = tokenize("Hello world, this is a test!")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_removes_stop_words(self):
        tokens = tokenize("This is the test of a thing")
        assert "this" not in tokens
        assert "is" not in tokens
        assert "the" not in tokens
        assert "test" in tokens
        assert "thing" in tokens

    def test_german_stop_words(self):
        tokens = tokenize("Das ist ein Test der Informatik")
        assert "das" not in tokens
        assert "ist" not in tokens
        assert "ein" not in tokens
        assert "test" in tokens
        assert "informatik" in tokens

    def test_removes_short_tokens(self):
        tokens = tokenize("I am a AI researcher")
        # Single-char tokens are removed
        assert "i" not in tokens

    def test_removes_digits(self):
        tokens = tokenize("In 2024 there were 100 papers")
        assert "2024" not in tokens
        assert "100" not in tokens
        assert "papers" in tokens


class TestBM25Encoder:
    def test_encode_document(self):
        enc = BM25Encoder()
        indices, values = enc.encode_document("machine learning is great")
        assert len(indices) > 0
        assert len(indices) == len(values)
        assert all(v > 0 for v in values)

    def test_encode_query_uses_existing_vocab(self):
        enc = BM25Encoder()
        enc.encode_document("machine learning is great for research")
        indices, values = enc.encode_query("machine research")
        # Should find both tokens in vocab
        assert len(indices) == 2

    def test_encode_query_ignores_unknown_tokens(self):
        enc = BM25Encoder()
        enc.encode_document("machine learning")
        indices, values = enc.encode_query("unknown token xyz")
        assert len(indices) == 0

    def test_vocab_persistence(self, tmp_path):
        enc = BM25Encoder()
        enc.encode_document("test document with some words")
        path = tmp_path / "vocab.json"
        enc.save(path)

        enc2 = BM25Encoder()
        enc2.load(path)
        assert enc.vocab == enc2.vocab

    def test_deterministic(self):
        enc = BM25Encoder()
        i1, v1 = enc.encode_document("same text here")
        i2, v2 = enc.encode_document("same text here")
        assert sorted(zip(i1, v1)) == sorted(zip(i2, v2))
