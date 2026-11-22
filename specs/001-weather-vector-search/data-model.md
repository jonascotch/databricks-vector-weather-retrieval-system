# Data Model: Weather Vector Search

## Overview

Persist shared saved locations separately from globally deduplicated source documents,
and keep searchable chunks in their own table. A join table records which saved
locations currently use each source document. Weather documents carry the lifecycle
state used to prevent stale or incomplete chunks from being returned. Search matches
are projections of a document, its current chunk, and all current saved-location
associations; they are not stored independently.

The tables are created in the PostgreSQL `weather` schema by the shared, idempotent
`sql/weather_schema.sql` script. The same DDL targets local PostgreSQL/pgvector and
Lakebase; owner/admin setup and app-role grants are described in [deployment.md](deployment.md).

## Entities

### SavedLocation (`weather_saved_locations`)

| Field | Type | Rules |
|---|---|---|
| id | UUID, primary key | Stable saved-location identifier used by the frontend and sync request. |
| input_type | text | `city_state` or `coordinates`. |
| input_value | jsonb | Validated city/state string or `{lat, lon}` pair entered by the user. |
| display_location | text | Readable city/state label or normalized coordinate label. |
| latitude, longitude | decimal, nullable | Resolved coordinates; valid geographic ranges and NWS coverage required before sync. |
| grid_id | text, nullable | NWS office/grid identifier returned by `/points`. |
| grid_x, grid_y | integer, nullable | NWS grid coordinates returned by `/points`. |
| created_at | timestamptz | Time the shared location was added. |

City/state lookup uses normalized exact name plus two-letter state abbreviation against
the pinned U.S. Census Gazetteer Places file. No match or multiple matches are a
validation error; the caller can resubmit using coordinates. Resolve and persist the
NWS grid tuple when syncing. All authorized users of the same application deployment
read and manage the same saved-location rows.

For each location within a sync operation, count every outbound NWS request attempt,
including retries, against an independent budget of 20. When the budget is exhausted,
stop requests for that location and report a partial outcome with the budget-exhausted
indicator.
Requests made for another location do not use this budget.

### WeatherLocationDocument (`weather_location_documents`)

| Field | Type | Rules |
|---|---|---|
| saved_location_id | UUID, foreign key | References `weather_saved_locations.id` with `ON DELETE CASCADE`. |
| document_id | text, foreign key | References `weather_documents.id` with `ON DELETE CASCADE`. |

Use `(saved_location_id, document_id)` as the primary key. One source document can be
associated with more than one saved location, and one saved location can reference many
documents. This preserves global NWS alert identity and prevents deleting a shared alert
when only one of its saved locations is removed.

Constraints and indexes:

- Foreign keys cascade association-row deletion when either a saved location or source
  document is deleted.
- The composite primary key prevents duplicate associations.
- Add a B-tree index on `document_id` for orphan cleanup and document-to-location joins.

### WeatherDocument (`weather_documents`)

| Field | Type | Rules |
|---|---|---|
| id | text, primary key | Alert: stable NWS feature ID. Forecast: deterministic key from NWS grid tuple, cadence (`daily`/`hourly`), and period start/end. |
| grid_id, grid_x, grid_y | text/integer, nullable | NWS grid point for forecast source identity; null for alerts. |
| source_type | text | Required; `alert` or `forecast`. |
| forecast_type | text, nullable | `daily` or `hourly` for forecast rows; null for alerts. |
| headline | text, nullable | NWS alert headline/event or forecast period name. |
| narrative_text | text | Required non-empty text to embed. Alert description and instruction are labeled and joined when present; forecast uses `detailedForecast`. |
| narrative_hash | text | Hash of normalized embeddable text, used to detect changes. |
| issued_at | timestamptz, nullable | Provider issue time when available. |
| effective_at | timestamptz, nullable | Alert effective time when available. |
| period_start, period_end | timestamptz, nullable | Required for forecast identity; null for alerts. |
| payload | jsonb | Original NWS source object for provenance. |
| synced_at | timestamptz | Time the normalized item was synchronized. |
| embedding_status | text | `pending`, `current`, or `stale`; default `pending`. Search includes only `current`. |

**Identity and update rules**

- Alert documents upsert by NWS feature ID.
- Forecast documents upsert by NWS grid tuple, forecast type, period start, and period end.
- A document is associated with each saved location for which it was synchronized.
- Documents remain globally deduplicated; a shared alert or forecast can be linked to
  multiple saved locations.
- A narrative change updates the document and sets `embedding_status = 'stale'`.
- Metadata-only updates that do not change normalized narrative do not invalidate vectors.
- A change to the active model revision or chunking configuration marks affected
  documents stale; vectors from incompatible model/configuration revisions are never
  mixed in a search.
- Keep only the current forecast record for a location/type/period; historical versions
  are out of scope.

### WeatherEmbedding (`weather_embeddings`)

| Field | Type | Rules |
|---|---|---|
| id | bigint identity, primary key | Generated per chunk row. |
| document_id | text, foreign key | References `weather_documents.id`; deletion MUST cascade to all chunk/vector rows. |
| chunk_index | integer | Zero-based, deterministic chunk order. |
| chunk_text | text | Non-empty narrative segment, produced with 800-character size and 100-character overlap. |
| chunk_hash | text | Hash of exact chunk text; used to reuse unchanged vectors. |
| embedding | `vector(384)` | Output from the pinned `all-MiniLM-L6-v2` revision. |
| model_name | text | Model identifier used to create the vector. |
| model_revision | text | Immutable revision associated with the embedding run. |
| created_at | timestamptz | Time the vector was created. |

Constraints and indexes:

- Foreign key from `document_id` to `weather_documents.id`.
- Unique `(document_id, chunk_index)` to make writes idempotent.
- B-tree index on `document_id` for reconciliation and joins.
- HNSW index on `embedding` with `vector_cosine_ops` for cosine search.
- The `vector` extension must be enabled before creating the vector column and HNSW
  index; Lakebase may require a database owner/admin to enable it.

### SearchMatch

Transient result fields: `document_id`, `source_type`, `locations` (all display labels
from current `weather_location_documents` associations), `headline`, `chunk_text`,
`similarity`, `synced_at`, `issued_at`, `effective_at`, `period_start`, and `period_end`.
Similarity is `1 - (embedding <=> query_vector)`. Each timestamp field is present in the
response; unavailable or inapplicable values are null. Only embeddings joined to
documents with `embedding_status = 'current'`, at least one current saved-location
association, and the active model revision are eligible.

### FrontendSession

Ephemeral browser state containing synchronization outcomes, current search text, and
displayed search matches. The shared saved-location list is loaded from
`weather_saved_locations`; only saved-location IDs are included in sync requests.

## Saved-location removal

During sync, upsert each source document and add its saved-location association in the
same transaction. Remove a saved location, its associations, and any documents that
become unreferenced in one database transaction. Delete orphaned `weather_documents`
rows after removing the location's associations; the
`weather_embeddings.document_id` foreign key cascades deletion to all corresponding
chunks and vectors. If another saved location still references a document, retain the
document and its chunks and return only the remaining location labels in search results.
A failure rolls back the complete operation so the location and associations remain
available for retry.

## Lifecycle

```text
new document ──> pending ──successful embedding──> current
                     │                              │
                     │ retry after failure          │ narrative changes
                     └─────────────> pending         v
                                                  stale
                                                    │
                               reconcile succeeds ─┘──> current
```

During reconciliation, compute all required embeddings before the write transaction.
Match unchanged chunks by ordered chunk hash and model revision, including when a
content edit shifts their ordinal. In one database transaction, move matched rows to
temporary non-conflicting indexes, delete obsolete chunk rows, assign the new indexes
to matched rows, insert changed/new rows, verify the expected chunk set, and set the
document to `current`. Before commit, verify the document narrative hash still matches
the hash used for embedding and that the model/chunking configuration is still active;
otherwise roll back. Search excludes the document while it is `pending` or `stale`. A
failed computation or transaction leaves it excluded and retryable.

Search aggregates associated saved-location labels for each document. It excludes
documents without a current location association, even if an embedding row remains due
to an inconsistent or interrupted legacy state. Aggregate labels before applying the
result limit so a document linked to multiple locations still produces one search match
per chunk.

## Validation Rules

- Reject coordinates outside geographic bounds or outside NWS-supported locations.
- Reject city/state values that are missing, ambiguous, or absent from the Gazetteer.
- Do not create a document without usable narrative text.
- Require forecast period start/end for forecast IDs.
- Require NWS grid ID and grid coordinates for forecast documents.
- Require each searchable document to have at least one saved-location association.
- Require location removal to remove the location and garbage-collect unreferenced
  documents and their chunks in one transaction.
- Require exactly 384 numeric vector values and the configured model revision.
- Require the active model revision and chunking configuration for every searchable
  document.
- Reject non-finite vector values before database write.
- Require `chunk_index >= 0`; enforce one row per document/chunk index.
- For search, require a non-empty query and a numeric `top_k`; default to 5 and clamp
  numeric values to 1–20.
