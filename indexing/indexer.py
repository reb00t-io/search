"""Qdrant indexing — builds the search collection with dense + sparse vectors."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models

from indexing.bm25 import BM25Encoder
from indexing.embedder import embed_documents, get_embedding_dim

logger = logging.getLogger(__name__)

COLLECTION_NAME = "documents"
BATCH_SIZE = 32


def _stable_uuid(doc_id: str) -> str:
    """Generate a stable UUID from document ID for Qdrant point ID."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id))


def ensure_collection(client: QdrantClient, embedding_dim: int):
    """Create the Qdrant collection if it doesn't exist."""
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in collections:
        logger.info("Collection '%s' already exists", COLLECTION_NAME)
        return False

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            "dense": models.VectorParams(
                size=embedding_dim,
                distance=models.Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            "bm25": models.SparseVectorParams(
                modifier=models.Modifier.IDF,
            ),
        },
    )

    # Create payload indices for filtering
    client.create_payload_index(COLLECTION_NAME, "language", models.PayloadSchemaType.KEYWORD)
    client.create_payload_index(COLLECTION_NAME, "source", models.PayloadSchemaType.KEYWORD)
    client.create_payload_index(COLLECTION_NAME, "doc_id", models.PayloadSchemaType.KEYWORD)

    logger.info("Created collection '%s' (dense=%d-dim + BM25 sparse)", COLLECTION_NAME, embedding_dim)
    return True


def index_records(
    client: QdrantClient,
    records: list[dict],
    bm25: BM25Encoder,
    content_reader,
    embedding_model: str = "intfloat/multilingual-e5-base",
) -> int:
    """Index a list of records into Qdrant. Returns count of indexed documents.

    Args:
        content_reader: callable(content_hash) -> str, reads content by hash
    """
    indexed = 0

    for batch_start in range(0, len(records), BATCH_SIZE):
        batch_records = records[batch_start : batch_start + BATCH_SIZE]

        texts = []
        valid_records = []
        for record in batch_records:
            try:
                text = content_reader(record["content_hash"])
                texts.append(text)
                valid_records.append(record)
            except FileNotFoundError:
                logger.warning("Missing content for %s", record["id"])

        if not texts:
            continue

        dense_vectors = embed_documents(texts, model_name=embedding_model)
        sparse_vectors = [bm25.encode_document(t) for t in texts]

        points = []
        for record, text, dense_vec, (sparse_indices, sparse_values) in zip(
            valid_records, texts, dense_vectors, sparse_vectors
        ):
            point_id = _stable_uuid(record["id"])
            snippet_text = text[:2000] if len(text) > 2000 else text

            payload = {
                "doc_id": record["id"],
                "source": record["source"],
                "title": record["title"],
                "url": record["url"],
                "language": record["language"],
                "text": snippet_text,
                "timestamp": record["timestamp"],
                "content_hash": record["content_hash"],
                "metadata": record.get("metadata", {}),
            }

            vectors = {"dense": dense_vec}
            sparse = {}
            if sparse_indices:
                sparse["bm25"] = models.SparseVector(
                    indices=sparse_indices,
                    values=sparse_values,
                )

            points.append(models.PointStruct(
                id=point_id,
                vector={**vectors, **sparse},
                payload=payload,
            ))

        client.upsert(collection_name=COLLECTION_NAME, points=points)
        indexed += len(points)
        logger.info("Indexed batch of %d points (%d total in this run)", len(points), indexed)

    return indexed


# Keep for backwards compatibility / full rebuild
def create_collection(client: QdrantClient, embedding_dim: int):
    """Drop and recreate collection (full rebuild)."""
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in collections:
        logger.info("Deleting existing collection: %s", COLLECTION_NAME)
        client.delete_collection(COLLECTION_NAME)

    ensure_collection(client, embedding_dim)


def index_documents(
    client: QdrantClient,
    data_dir: str | Path,
    bm25: BM25Encoder,
    embedding_model: str = "intfloat/multilingual-e5-base",
):
    """Read all filtered documents and index into Qdrant (full rebuild)."""
    from ingestion.storage import ContentStore

    data_dir = Path(data_dir)
    store = ContentStore(data_dir)

    filtered_path = data_dir / "filtered" / "documents.jsonl"
    records = store.load_records(filtered_path)
    logger.info("Indexing %d filtered documents", len(records))

    indexed = index_records(client, records, bm25, store.read_content, embedding_model)
    logger.info("Indexing complete: %d documents in collection '%s'", indexed, COLLECTION_NAME)
