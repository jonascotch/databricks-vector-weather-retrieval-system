"""Reconcile embeddings for weather documents that are not current."""

import logging

import psycopg2

from lakebase import (
    EmbeddingDocumentChangedError,
    EmbeddingDocumentUnavailableError,
    EmbeddingReconciliationRetryError,
    get_embedding_documents,
    reconcile_document_embeddings,
)
from weather_embeddings import (
    MODEL_NAME,
    MODEL_REVISION,
    chunk_narrative,
    encode_texts,
)


logger = logging.getLogger(__name__)


def _is_current(document: dict, chunks: list[dict]) -> bool:
    existing = document["embeddings"]
    return (
        document["embedding_status"] == "current"
        and len(existing) == len(chunks)
        and all(
            row["chunk_index"] == index
            and row["chunk_text"] == chunk["text"]
            and row["chunk_hash"] == chunk["hash"]
            and row["model_name"] == MODEL_NAME
            and row["model_revision"] == MODEL_REVISION
            for index, (row, chunk) in enumerate(zip(existing, chunks))
        )
    )


def reconcile_pending_embeddings() -> dict:
    """Embed every non-current weather document and return a summary.

    Documents that changed concurrently are skipped with a warning. Encoder and
    database failures propagate to the caller as OSError/RuntimeError/ValueError
    or psycopg2.Error so the synchronization route can degrade gracefully.
    """
    documents = get_embedding_documents()
    documents_processed = 0
    chunks_reused = 0
    chunks_created = 0

    for document in documents:
        chunks = chunk_narrative(document["narrative_text"])
        if _is_current(document, chunks):
            continue

        existing = document["embeddings"]
        available = {}
        for row in existing:
            if (
                row["model_name"] == MODEL_NAME
                and row["model_revision"] == MODEL_REVISION
            ):
                available.setdefault((row["chunk_hash"], row["chunk_text"]), []).append(row)

        reusable_count = {}
        for index, chunk in enumerate(chunks):
            key = (chunk["hash"], chunk["text"])
            rows = available.get(key, [])
            if rows:
                reusable_count[index] = rows.pop()

        missing_indices = [
            index for index in range(len(chunks)) if index not in reusable_count
        ]
        try:
            missing_vectors = encode_texts(
                [chunks[index]["text"] for index in missing_indices]
            )
        except (OSError, RuntimeError, ValueError) as error:
            logger.error(
                "Embedding failed: category=encoder document_id=%s error_type=%s",
                document["id"],
                type(error).__name__,
            )
            raise
        encoded_vectors = dict(zip(missing_indices, missing_vectors))

        try:
            result = reconcile_document_embeddings(
                document["id"],
                document["narrative_hash"],
                chunks,
                encoded_vectors,
                MODEL_NAME,
                MODEL_REVISION,
            )
        except (
            EmbeddingDocumentChangedError,
            EmbeddingDocumentUnavailableError,
            EmbeddingReconciliationRetryError,
        ) as error:
            logger.warning(
                "Embedding skipped: category=concurrent_change document_id=%s error_type=%s",
                document["id"],
                type(error).__name__,
            )
            continue
        except RuntimeError as error:
            logger.error(
                "Embedding failed: category=reconciliation document_id=%s error_type=%s",
                document["id"],
                type(error).__name__,
            )
            raise
        except psycopg2.Error as error:
            logger.error(
                "Embedding failed: category=database document_id=%s error_type=%s",
                document["id"],
                type(error).__name__,
            )
            raise

        documents_processed += 1
        chunks_reused += result["chunks_reused"]
        chunks_created += result["chunks_created"]
        logger.info(
            "Embedding reconciled: document_id=%s chunks_reused=%d chunks_created=%d",
            document["id"],
            result["chunks_reused"],
            result["chunks_created"],
        )

    summary = {
        "documents_processed": documents_processed,
        "chunks_reused": chunks_reused,
        "chunks_created": chunks_created,
    }
    logger.info(
        "Embedding run complete: documents=%d chunks_reused=%d chunks_created=%d",
        documents_processed,
        chunks_reused,
        chunks_created,
    )
    return summary
