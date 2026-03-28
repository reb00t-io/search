"""Dense embedding using sentence-transformers."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Lazy-loaded model
_model = None
_model_name = None


def get_model(model_name: str = "intfloat/multilingual-e5-base"):
    """Get or lazily load the embedding model."""
    global _model, _model_name
    if _model is None or _model_name != model_name:
        logger.info("Loading embedding model: %s", model_name)
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(model_name)
        _model_name = model_name
        logger.info("Model loaded (dim=%d)", _model.get_sentence_embedding_dimension())
    return _model


def embed_documents(texts: list[str], model_name: str = "intfloat/multilingual-e5-base") -> list[list[float]]:
    """Embed document texts. Prepends 'passage: ' as required by E5 models."""
    model = get_model(model_name)
    prefixed = [f"passage: {t}" for t in texts]
    embeddings = model.encode(prefixed, show_progress_bar=False, normalize_embeddings=True)
    return [e.tolist() for e in embeddings]


def embed_query(text: str, model_name: str = "intfloat/multilingual-e5-base") -> list[float]:
    """Embed a query. Prepends 'query: ' as required by E5 models."""
    model = get_model(model_name)
    embedding = model.encode(f"query: {text}", show_progress_bar=False, normalize_embeddings=True)
    return embedding.tolist()


def get_embedding_dim(model_name: str = "intfloat/multilingual-e5-base") -> int:
    """Return embedding dimensionality."""
    model = get_model(model_name)
    return model.get_sentence_embedding_dimension()
