"""Deterministic weather narrative chunking and embedding."""

from functools import lru_cache
import hashlib
import logging
import math


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
EMBEDDING_DIMENSION = 384
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
logger = logging.getLogger(__name__)


def chunk_narrative(text: str) -> list[dict]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Narrative text must be a non-empty string.")
    normalized = text.strip()
    step = CHUNK_SIZE - CHUNK_OVERLAP
    chunks = []
    for start in range(0, len(normalized), step):
        chunk_text = normalized[start : start + CHUNK_SIZE]
        if not chunk_text:
            break
        chunks.append(
            {
                "text": chunk_text,
                "hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
            }
        )
        if start + CHUNK_SIZE >= len(normalized):
            break
    return chunks


@lru_cache(maxsize=1)
def get_encoder():
    from sentence_transformers import SentenceTransformer

    encoder = SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION)
    if hasattr(encoder, "get_embedding_dimension"):
        embedding_dimension = encoder.get_embedding_dimension()
    else:
        embedding_dimension = encoder.get_sentence_embedding_dimension()
    if embedding_dimension != EMBEDDING_DIMENSION:
        logger.error(
            "Embedding model rejected: category=dimension model=%s revision=%s",
            MODEL_NAME,
            MODEL_REVISION,
        )
        raise RuntimeError(
            f"The configured embedding model must return {EMBEDDING_DIMENSION} dimensions."
        )
    return encoder


def encode_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    encoded = get_encoder().encode(
        texts,
        batch_size=32,
        convert_to_numpy=True,
        normalize_embeddings=False,
        show_progress_bar=False,
    )
    vectors = []
    for vector in encoded:
        values = [float(value) for value in vector]
        if len(values) != EMBEDDING_DIMENSION:
            logger.error(
                "Embedding rejected: category=dimension expected=%d actual=%d",
                EMBEDDING_DIMENSION,
                len(values),
            )
            raise ValueError(
                f"Embedding must contain exactly {EMBEDDING_DIMENSION} values."
            )
        if not all(math.isfinite(value) for value in values):
            logger.error("Embedding rejected: category=non_finite_values")
            raise ValueError("Embedding values must all be finite numbers.")
        vectors.append(values)
    if len(vectors) != len(texts):
        logger.error(
            "Embedding rejected: category=vector_count expected=%d actual=%d",
            len(texts),
            len(vectors),
        )
        raise ValueError("The encoder returned an unexpected number of vectors.")
    return vectors


def encode_query(text: str) -> list[float]:
    return encode_texts([text])[0]
