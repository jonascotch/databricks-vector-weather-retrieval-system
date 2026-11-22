# Data Model: Weather Vector Search

## Overview

Persist source documents separately from their searchable chunks. Weather documents
carry the lifecycle state used to prevent stale or incomplete chunks from being returned.
Search matches are projections of a document and one of its current chunks; they are not
stored independently.

The tables are created in the PostgreSQL `weather` schema by the shared, idempotent
`sql/weather_schema.sql` script. The same DDL targets local PostgreSQL/pgvector and
Lakebase; owner/admin setup and app-role grants are described in [deployment.md](deployment.md).

## Entities

### WeatherLocation

| Field | Type | Rules |
|---|---|---|
| input | string or object | City/state string or `{lat, lon}` coordinates. |
| latitude, longitude | decimal | Required after resolution; must be within valid geographic ranges and inside NWS coverage. Normalize to no more than four decimal places for NWS `/points`. |
| grid_id | string | NWS office/grid identifier returned by `/points`. |
| grid_x, grid_y | integer | NWS grid coordinates returned by `/points`. |
| display_location | string | Resolved city/state label or normalized coordinate label. |

City/state lookup uses normalized exact name plus two-letter state abbreviation against
the pinned U.S. Census Gazetteer Places file. No match or multiple matches are a
validation error; the caller can resubmit using coordinates. The resolved NWS grid tuple
is the canonical identity for forecast upserts.

For each location within a sync operation, count every outbound NWS request attempt,
including retries, against an independent budget of 20. When the budget is exhausted,
stop requests for that location and report a partial outcome with the budget-exhausted
indicator.
Requests made for another location do not use this budget.

### WeatherDocument (`weather_documents`)

| Field | Type | Rules |
|---|---|---|
| id | text, primary key | Alert: stable NWS feature ID. Forecast: deterministic key from NWS grid tuple, cadence (`daily`/`hourly`), and period start/end. |
| location | text | Required display location. |
| latitude, longitude | numeric | Resolved source point. |
| grid_id, grid_x, grid_y | text/integer | Resolved NWS grid point used for source retrieval. |
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
| document_id | text, foreign key | References `weather_documents.id`; cascading delete is acceptable. |
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

Transient result fields: `document_id`, `source_type`, `location`, `headline`,
`chunk_text`, `similarity`, `synced_at`, `issued_at`, `effective_at`, `period_start`,
and `period_end`. Similarity is `1 - (embedding <=> query_vector)`. Each timestamp field
is present in the response; unavailable or inapplicable values are null. Only embeddings
joined to documents with `embedding_status = 'current'` and created with the active
model revision are eligible.

### FrontendSession

Ephemeral browser state containing the user's selected locations, synchronization
outcomes, current search text, and displayed search matches. It is not persisted as a
separate database entity.

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

## Validation Rules

- Reject coordinates outside geographic bounds or outside NWS-supported locations.
- Reject city/state values that are missing, ambiguous, or absent from the Gazetteer.
- Do not create a document without usable narrative text.
- Require forecast period start/end for forecast IDs.
- Require exactly 384 numeric vector values and the configured model revision.
- Require the active model revision and chunking configuration for every searchable
  document.
- Reject non-finite vector values before database write.
- Require `chunk_index >= 0`; enforce one row per document/chunk index.
- For search, require a non-empty query and a numeric `top_k`; default to 5 and clamp
  numeric values to 1–20.
