"""Database operations shared by local PostgreSQL and Lakebase."""

from contextlib import contextmanager
from collections import defaultdict, deque
import logging
import os
from uuid import UUID, uuid4

import psycopg2
from pgvector import Vector
from pgvector.psycopg2 import register_vector
from psycopg2.extras import Json, RealDictCursor, execute_values, register_uuid


logger = logging.getLogger(__name__)


class SavedLocationNotFoundError(Exception):
    """Raised when an operation references a location that is not saved."""


class EmbeddingDocumentChangedError(Exception):
    """Raised when a source narrative changes while its chunks are being computed."""


class EmbeddingDocumentUnavailableError(Exception):
    """Raised when a document no longer has a saved-location association."""


class EmbeddingReconciliationRetryError(Exception):
    """Raised when a concurrent embedding run invalidates the computed chunk set."""


def _connect():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        connection = psycopg2.connect(
            database_url,
            cursor_factory=RealDictCursor,
        )
    else:
        endpoint = os.environ["LAKEBASE_ENDPOINT"]
        from databricks.sdk import WorkspaceClient

        credential = WorkspaceClient().postgres.generate_database_credential(
            endpoint=endpoint
        )
        connection = psycopg2.connect(
            host=os.environ["PGHOST"],
            port=int(os.getenv("PGPORT", "5432")),
            dbname=os.environ["PGDATABASE"],
            user=os.environ["PGUSER"],
            password=credential.token,
            sslmode="require",
            application_name=os.getenv("PGAPPNAME", "weather-vector-search"),
            cursor_factory=RealDictCursor,
        )
    register_vector(connection)
    register_uuid(conn_or_curs=connection)
    return connection


@contextmanager
def get_connection():
    """Yield a cursor-enabled connection and commit or roll back its work."""
    connection = None
    try:
        connection = _connect()
        yield connection
        connection.commit()
    except psycopg2.Error as error:
        if connection is not None:
            connection.rollback()
        logger.error(
            "Database operation failed: category=postgres error_type=%s",
            type(error).__name__,
        )
        raise
    except BaseException:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def list_saved_locations() -> list[dict]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, input_type, input_value, display_location,
                       latitude, longitude, grid_id, grid_x, grid_y, created_at
                FROM weather.weather_saved_locations
                ORDER BY created_at, id
                """
            )
            return list(cursor.fetchall())


def get_saved_location(location_id: UUID) -> dict | None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, input_type, input_value, display_location,
                       latitude, longitude, grid_id, grid_x, grid_y, created_at
                FROM weather.weather_saved_locations
                WHERE id = %s
                """,
                (location_id,),
            )
            return cursor.fetchone()


def search_weather_places(query: str, limit: int) -> list[dict]:
    """Return Gazetteer places whose normalized names begin with the query."""
    escaped = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT name, state, latitude, longitude
                FROM weather.weather_places
                WHERE name_normalized LIKE %s ESCAPE '\\'
                ORDER BY length(name_normalized), name_normalized, state
                LIMIT %s
                """,
                (escaped + "%", limit),
            )
            return list(cursor.fetchall())


def create_saved_location(
    input_type: str, input_value: object, display_location: str
) -> dict:
    location_id = uuid4()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO weather.weather_saved_locations (
                    id, input_type, input_value, display_location
                )
                VALUES (%s, %s, %s, %s)
                RETURNING id, input_type, input_value, display_location,
                          latitude, longitude, grid_id, grid_x, grid_y, created_at
                """,
                (location_id, input_type, Json(input_value), display_location),
            )
            return cursor.fetchone()


def update_saved_location_resolution(
    location_id: UUID, resolved: dict
) -> dict:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE weather.weather_saved_locations
                SET latitude = %s, longitude = %s,
                    grid_id = COALESCE(%s, grid_id),
                    grid_x = COALESCE(%s, grid_x),
                    grid_y = COALESCE(%s, grid_y)
                WHERE id = %s
                RETURNING id, input_type, input_value, display_location,
                          latitude, longitude, grid_id, grid_x, grid_y, created_at
                """,
                (
                    resolved["latitude"],
                    resolved["longitude"],
                    resolved.get("grid_id"),
                    resolved.get("grid_x"),
                    resolved.get("grid_y"),
                    location_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise SavedLocationNotFoundError("Saved location no longer exists.")
            return row


def delete_saved_location(location_id: UUID) -> dict:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM weather.weather_saved_locations WHERE id = %s FOR UPDATE",
                (location_id,),
            )
            if cursor.fetchone() is None:
                raise SavedLocationNotFoundError("Saved location does not exist.")

            cursor.execute(
                """
                SELECT document_id
                FROM weather.weather_location_documents
                WHERE saved_location_id = %s
                ORDER BY document_id
                """,
                (location_id,),
            )
            document_ids = [row["document_id"] for row in cursor.fetchall()]

            if document_ids:
                cursor.execute(
                    """
                    SELECT id
                    FROM weather.weather_documents
                    WHERE id = ANY(%s)
                    ORDER BY id
                    FOR UPDATE
                    """,
                    (document_ids,),
                )
                cursor.fetchall()

            cursor.execute(
                "DELETE FROM weather.weather_location_documents WHERE saved_location_id = %s",
                (location_id,),
            )
            cursor.execute(
                "DELETE FROM weather.weather_saved_locations WHERE id = %s",
                (location_id,),
            )

            orphan_ids = []
            if document_ids:
                cursor.execute(
                    """
                    SELECT documents.id
                    FROM weather.weather_documents AS documents
                    WHERE documents.id = ANY(%s)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM weather.weather_location_documents AS associations
                          WHERE associations.document_id = documents.id
                      )
                    ORDER BY documents.id
                    """,
                    (document_ids,),
                )
                orphan_ids = [row["id"] for row in cursor.fetchall()]

            chunks_deleted = 0
            if orphan_ids:
                cursor.execute(
                    """
                    SELECT count(*) AS count
                    FROM weather.weather_embeddings
                    WHERE document_id = ANY(%s)
                    """,
                    (orphan_ids,),
                )
                chunks_deleted = cursor.fetchone()["count"]
                cursor.execute(
                    "DELETE FROM weather.weather_documents WHERE id = ANY(%s)",
                    (orphan_ids,),
                )

            return {
                "location_id": str(location_id),
                "documents_deleted": len(orphan_ids),
                "chunks_deleted": chunks_deleted,
                "shared_documents_retained": len(document_ids) - len(orphan_ids),
            }


def upsert_weather_documents(
    documents: list[dict], saved_location_id: UUID
) -> int:
    """Upsert documents and their location associations in one transaction."""
    unique_documents = {document["id"]: document for document in documents}
    if not unique_documents:
        return 0

    ordered_documents = [
        unique_documents[document_id] for document_id in sorted(unique_documents)
    ]
    values = [
        (
            document["id"],
            document.get("grid_id"),
            document.get("grid_x"),
            document.get("grid_y"),
            document["source_type"],
            document.get("forecast_type"),
            document.get("headline"),
            document["narrative_text"],
            document["narrative_hash"],
            document.get("issued_at"),
            document.get("effective_at"),
            document.get("period_start"),
            document.get("period_end"),
            Json(document["payload"]),
            document["synced_at"],
        )
        for document in ordered_documents
    ]
    upsert_query = """
        INSERT INTO weather.weather_documents (
            id, grid_id, grid_x, grid_y, source_type, forecast_type, headline,
            narrative_text, narrative_hash, issued_at, effective_at, period_start,
            period_end, payload, synced_at
        ) VALUES %s
        ON CONFLICT (id) DO UPDATE SET
            grid_id = EXCLUDED.grid_id,
            grid_x = EXCLUDED.grid_x,
            grid_y = EXCLUDED.grid_y,
            source_type = EXCLUDED.source_type,
            forecast_type = EXCLUDED.forecast_type,
            headline = EXCLUDED.headline,
            narrative_text = EXCLUDED.narrative_text,
            narrative_hash = EXCLUDED.narrative_hash,
            issued_at = EXCLUDED.issued_at,
            effective_at = EXCLUDED.effective_at,
            period_start = EXCLUDED.period_start,
            period_end = EXCLUDED.period_end,
            payload = EXCLUDED.payload,
            synced_at = EXCLUDED.synced_at,
            embedding_status = CASE
                WHEN weather.weather_documents.narrative_hash
                     IS DISTINCT FROM EXCLUDED.narrative_hash
                THEN 'stale'
                ELSE weather.weather_documents.embedding_status
            END
    """
    association_values = [
        (saved_location_id, document["id"]) for document in ordered_documents
    ]
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM weather.weather_saved_locations
                WHERE id = %s
                FOR KEY SHARE
                """,
                (saved_location_id,),
            )
            if cursor.fetchone() is None:
                raise SavedLocationNotFoundError("Saved location no longer exists.")
            execute_values(cursor, upsert_query, values, page_size=100)
            execute_values(
                cursor,
                """
                INSERT INTO weather.weather_location_documents (
                    saved_location_id, document_id
                ) VALUES %s
                ON CONFLICT (saved_location_id, document_id) DO NOTHING
                """,
                association_values,
                page_size=100,
            )
    return len(ordered_documents)


def get_embedding_documents() -> list[dict]:
    """Return associated documents and their current chunk metadata."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT documents.id, documents.narrative_text,
                       documents.narrative_hash, documents.embedding_status
                FROM weather.weather_documents AS documents
                WHERE EXISTS (
                    SELECT 1
                    FROM weather.weather_location_documents AS associations
                    WHERE associations.document_id = documents.id
                )
                ORDER BY documents.id
                """
            )
            documents = list(cursor.fetchall())
            if not documents:
                return []

            document_ids = [document["id"] for document in documents]
            cursor.execute(
                """
                SELECT id, document_id, chunk_index, chunk_text, chunk_hash,
                       model_name, model_revision
                FROM weather.weather_embeddings
                WHERE document_id = ANY(%s)
                ORDER BY document_id, chunk_index
                """,
                (document_ids,),
            )
            embeddings_by_document = defaultdict(list)
            for embedding in cursor.fetchall():
                embeddings_by_document[embedding["document_id"]].append(embedding)
            for document in documents:
                document["embeddings"] = embeddings_by_document[document["id"]]
            return documents


def reconcile_document_embeddings(
    document_id: str,
    narrative_hash: str,
    chunks: list[dict],
    encoded_vectors: dict[int, list[float]],
    model_name: str,
    model_revision: str,
) -> dict:
    """Atomically reconcile one document's complete active chunk set."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT narrative_hash
                FROM weather.weather_documents
                WHERE id = %s
                FOR UPDATE
                """,
                (document_id,),
            )
            document = cursor.fetchone()
            if document is None:
                raise EmbeddingDocumentUnavailableError(
                    "The document no longer exists."
                )
            if document["narrative_hash"] != narrative_hash:
                raise EmbeddingDocumentChangedError(
                    "The document narrative changed during embedding."
                )
            cursor.execute(
                """
                SELECT 1
                FROM weather.weather_location_documents
                WHERE document_id = %s
                LIMIT 1
                """,
                (document_id,),
            )
            if cursor.fetchone() is None:
                raise EmbeddingDocumentUnavailableError(
                    "The document no longer has a saved location."
                )

            cursor.execute(
                """
                SELECT id, chunk_index, chunk_text, chunk_hash,
                       model_name, model_revision
                FROM weather.weather_embeddings
                WHERE document_id = %s
                ORDER BY chunk_index
                FOR UPDATE
                """,
                (document_id,),
            )
            existing = list(cursor.fetchall())
            reusable = _reusable_chunk_rows(
                chunks, existing, model_name, model_revision
            )
            missing_indices = [
                index for index in range(len(chunks)) if index not in reusable
            ]
            if any(index not in encoded_vectors for index in missing_indices):
                raise EmbeddingReconciliationRetryError(
                    "A concurrent embedding run changed the reusable chunk set."
                )

            max_index = max(
                (row["chunk_index"] for row in existing), default=-1
            )
            temporary_offset = max_index + len(chunks) + 1
            for index, row in reusable.items():
                cursor.execute(
                    """
                    UPDATE weather.weather_embeddings
                    SET chunk_index = %s
                    WHERE id = %s
                    """,
                    (temporary_offset + index, row["id"]),
                )

            reusable_ids = [row["id"] for row in reusable.values()]
            if reusable_ids:
                cursor.execute(
                    """
                    DELETE FROM weather.weather_embeddings
                    WHERE document_id = %s
                      AND NOT (id = ANY(%s))
                    """,
                    (document_id, reusable_ids),
                )
            else:
                cursor.execute(
                    "DELETE FROM weather.weather_embeddings WHERE document_id = %s",
                    (document_id,),
                )

            for index, row in reusable.items():
                cursor.execute(
                    """
                    UPDATE weather.weather_embeddings
                    SET chunk_index = %s, chunk_text = %s, chunk_hash = %s
                    WHERE id = %s
                    """,
                    (index, chunks[index]["text"], chunks[index]["hash"], row["id"]),
                )

            insert_values = [
                (
                    document_id,
                    index,
                    chunks[index]["text"],
                    chunks[index]["hash"],
                    Vector(encoded_vectors[index]),
                    model_name,
                    model_revision,
                )
                for index in missing_indices
            ]
            if insert_values:
                execute_values(
                    cursor,
                    """
                    INSERT INTO weather.weather_embeddings (
                        document_id, chunk_index, chunk_text, chunk_hash,
                        embedding, model_name, model_revision
                    ) VALUES %s
                    """,
                    insert_values,
                    page_size=100,
                )

            cursor.execute(
                """
                SELECT chunk_index, chunk_text, chunk_hash, model_name, model_revision
                FROM weather.weather_embeddings
                WHERE document_id = %s
                ORDER BY chunk_index
                """,
                (document_id,),
            )
            actual = list(cursor.fetchall())
            expected = [
                {
                    "chunk_index": index,
                    "chunk_text": chunk["text"],
                    "chunk_hash": chunk["hash"],
                    "model_name": model_name,
                    "model_revision": model_revision,
                }
                for index, chunk in enumerate(chunks)
            ]
            if actual != expected:
                raise RuntimeError(
                    "Embedding reconciliation did not produce the expected chunk set."
                )

            cursor.execute(
                """
                UPDATE weather.weather_documents
                SET embedding_status = 'current'
                WHERE id = %s AND narrative_hash = %s
                """,
                (document_id, narrative_hash),
            )
            if cursor.rowcount != 1:
                raise EmbeddingDocumentChangedError(
                    "The document could not be marked current."
                )

    return {
        "chunks_reused": len(reusable),
        "chunks_created": len(missing_indices),
    }


def search_weather_embeddings(
    query_vector: list[float],
    top_k: int,
    model_name: str,
    model_revision: str,
) -> list[dict]:
    vector = Vector(query_vector)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH eligible AS (
                    SELECT
                        embeddings.id AS embedding_id,
                        embeddings.document_id,
                        embeddings.embedding,
                        embeddings.chunk_text,
                        documents.source_type,
                        documents.headline,
                        documents.synced_at,
                        documents.issued_at,
                        documents.effective_at,
                        documents.period_start,
                        documents.period_end,
                        array_agg(
                            DISTINCT locations.display_location
                            ORDER BY locations.display_location
                        ) AS locations
                    FROM weather.weather_embeddings AS embeddings
                    JOIN weather.weather_documents AS documents
                      ON documents.id = embeddings.document_id
                    JOIN weather.weather_location_documents AS associations
                      ON associations.document_id = documents.id
                    JOIN weather.weather_saved_locations AS locations
                      ON locations.id = associations.saved_location_id
                    WHERE documents.embedding_status = 'current'
                      AND embeddings.model_name = %s
                      AND embeddings.model_revision = %s
                    GROUP BY
                        embeddings.id, embeddings.document_id, embeddings.embedding,
                        embeddings.chunk_text, documents.source_type,
                        documents.headline, documents.synced_at, documents.issued_at,
                        documents.effective_at, documents.period_start,
                        documents.period_end
                )
                SELECT
                    document_id, source_type, locations, headline, chunk_text,
                    1 - (embedding <=> %s::vector) AS similarity,
                    synced_at, issued_at, effective_at, period_start, period_end
                FROM eligible
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (model_name, model_revision, vector, vector, top_k),
            )
            return list(cursor.fetchall())


def _reusable_chunk_rows(
    chunks: list[dict],
    existing: list[dict],
    model_name: str,
    model_revision: str,
) -> dict[int, dict]:
    available = defaultdict(deque)
    for row in existing:
        if row["model_name"] == model_name and row["model_revision"] == model_revision:
            available[(row["chunk_hash"], row["chunk_text"])].append(row)

    reusable = {}
    for index, chunk in enumerate(chunks):
        matching = available[(chunk["hash"], chunk["text"])]
        if matching:
            reusable[index] = matching.popleft()
    return reusable
