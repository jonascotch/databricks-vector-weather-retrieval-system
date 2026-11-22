# Tasks: Weather Vector Search

**Input**: Design documents from `specs/001-weather-vector-search/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`,
`contracts/weather-api.yaml`, `contracts/frontend.md`, `quickstart.md`, and
`deployment.md`

**Automated tests**: Not included. FR-027 waives automated tests for this learning
exercise; each story has manual acceptance criteria.

**Organization**: Tasks are grouped by the four P1 user stories. Setup and shared
database prerequisites precede story implementation.

**Path conventions**: The repository uses a root-level Flask application, with `sql/`,
`templates/`, `static/`, `notebooks/`, and `data/` at the repository root.

## Phase 1: Setup

**Purpose**: Establish dependencies, local configuration, and the development stack.

- [X] T001 [P] Add Flask, Gunicorn, requests, psycopg2, pgvector, sentence-transformers, and databricks-sdk to `requirements.txt`; do not add automated-test or test-only dependencies.
- [X] T002 [P] Add local variable placeholders and exclude secrets, downloaded Gazetteer data, and model caches in `.env.example`, `.gitignore`, and `.dockerignore`.
- [X] T003 Configure `Dockerfile` and `docker-compose.yml` for Python 3.11 and PostgreSQL/pgvector; wait for database health, use a named volume, mount `sql/weather_schema.sql` for fresh-volume initialization, publish only the app on `127.0.0.1`, and do not publish the database port.

## Phase 2: Foundational

**Purpose**: Establish the shared schema, safe schema upgrade, and database connection
used by all user stories.

- [X] T004 Create `sql/weather_schema.sql` with all fields in `data-model.md` for `weather_saved_locations`, `weather_documents`, `weather_location_documents`, and `weather_embeddings`; enforce `input_type IN ('city_state', 'coordinates')`, `latitude >= -90 AND latitude <= 90`, `longitude >= -180 AND longitude <= 180`, nullable unresolved coordinates/grid values, non-empty narratives/chunk text, `source_type IN ('alert', 'forecast')`, forecast type in `('daily', 'hourly')` with period/grid fields required, null alert forecast/period/grid fields, `embedding_status IN ('pending', 'current', 'stale')`, `chunk_index >= 0`, `vector(384)`, cascading document-to-embedding foreign key, unique `(document_id, chunk_index)`, the location/document composite key, and the cosine HNSW and join indexes.
- [X] T005 Create `sql/migrations/001_saved_location_associations.sql` as a versioned transaction that backfills saved locations and document associations from each distinct legacy location/coordinate/grid tuple; preserve existing document and embedding rows, relax legacy `NOT NULL` constraints needed by the new schema, leave legacy columns in place, and fail without partial migration if any document cannot be associated.
- [X] T006 [P] Implement `get_connection()` in `lakebase.py` for local `DATABASE_URL` and Databricks `PG*` settings; use `RealDictCursor`, refresh Lakebase OAuth credentials for each new connection, require Lakebase SSL, and never log secrets or tokens.

**Checkpoint**: Fresh and existing databases can provide persistent saved locations,
document associations, and the shared connection boundary.

## Phase 3: User Story 1 - Sync Weather for Selected Locations (Priority: P1)

**Goal**: Persist a shared location list, synchronize only saved locations, and remove
orphaned weather data when a location is deleted.

**Independent Test**: Add a location, reload the page, and confirm it remains. Sync it,
then remove it and confirm its association disappears and documents/chunks used only by
that location are deleted. If a document is shared with another saved location, confirm
it remains searchable only with the remaining location listed.

- [X] T007 [US1] Add saved-location list, create, and delete operations in `lakebase.py`; store UUID `id`, `input_type`, JSON `input_value`, display label, nullable resolved coordinates/grid fields, and creation time; in one transaction remove the location and associations, delete documents with no remaining association, cascade their chunks/vectors, preserve shared documents, and return deletion/retention counts.
- [X] T008 [US1] Implement `GET /weather/locations`, `POST /weather/locations`, and `DELETE /weather/locations/{location_id}` in `app.py`; validate city/state strings of at least 3 characters, require `lat` in `[-90, 90]` and `lon` in `[-180, 180]`, validate UUIDs, return the documented response/error shapes, and report a failed deletion without claiming success.
- [X] T009 [US1] Update `POST /weather/sync` in `app.py` to accept saved-location IDs, resolve each saved input, retain the 20-attempt-per-location limit and partial outcomes, and atomically upsert each source document with its `weather_location_documents` association while preserving global alert IDs and forecast grid/type/period identities.
- [X] T010 [US1] Update `templates/index.html`, `static/weather.js`, and `static/weather.css` to load the shared list on page open, add through `POST /weather/locations`, remove through `DELETE /weather/locations/{location_id}`, update the list only after server success, submit only saved IDs for sync, and display accessible validation, deletion-count, and operational feedback.

## Phase 4: User Story 2 - Prepare Weather Narratives for Search (Priority: P1)

**Goal**: Produce deterministic searchable chunks and vectors for associated weather
documents.

**Independent Test**: Sync a document and run the embedding process; confirm its chunks
are linked to it and it becomes searchable only after the complete chunk set is saved.
Run the process again and confirm unchanged chunks are not duplicated.

- [X] T011 [P] [US2] Implement deterministic 800-character sliding-window chunking with 100-character overlap, stable order and hashes, a pinned `sentence-transformers/all-MiniLM-L6-v2` revision, one encoder per process, and validation of exactly 384 finite numeric values in `weather_embeddings.py`.
- [X] T012 [US2] Implement `notebooks/ingest_weather_embeddings.py` to process only documents with saved-location associations, reuse unchanged chunks, batch writes through `lakebase.py`, and transactionally replace obsolete/changed chunks while setting a document current only after its complete expected chunk set is stored; do not use Spark JDBC.

## Phase 5: User Story 3 - Search Weather Narratives (Priority: P1)

**Goal**: Return ranked weather passages with complete provenance and all remaining
saved-location associations.

**Independent Test**: Search after embedding and confirm each chunk appears once, lists
all associated saved locations, and includes source/freshness fields; remove one
association and confirm it no longer appears while a shared document remains available
for another location.

- [X] T013 [US3] Implement `POST /weather/search` in `app.py`; validate query and `top_k`, default to 5 and clamp to 1–20, use the process-level encoder and `<=>`, filter to current documents with active location associations, aggregate all associated location labels before applying the result limit, and return each chunk once with the contract fields and nullable timestamps.
- [X] T014 [P] [US3] Update `templates/index.html`, `static/weather.js`, and `static/weather.css` to submit semantic queries, show ranked matches with all associated locations and source/freshness metadata, render provider text as text, and provide clear validation, request-error, and empty-result states.

## Phase 6: User Story 4 - Run Locally and Deploy to Databricks (Priority: P1)

**Goal**: Demonstrate the complete workflow locally and document its Lakebase-backed
Databricks deployment.

**Independent Test**: Follow the local guide without Databricks credentials, verify
location persistence across reloads and database-container recreation, and follow the
deployment guide to configure the shared schema and app permissions for Databricks.

- [X] T015 [P] [US4] Add root `app.yaml` to run Flask through Gunicorn on `0.0.0.0` and `DATABRICKS_APP_PORT`, configure non-secret settings including `LAKEBASE_ENDPOINT`, and use the Lakebase resource's managed app identity.
- [X] T016 [P] [US4] Create `README_WEATHER.md` documenting NWS source/limits, schema and migration, chunking/model dimensions, persistent shared-location management and deletion behavior, local Compose workflow, sync→embed→search, Lakebase deployment, access permissions, known limitations, and the testing waiver.
- [X] T017 [US4] Complete the manual workflows in `specs/001-weather-vector-search/quickstart.md` and `specs/001-weather-vector-search/deployment.md`; verify fresh-schema setup, legacy-data migration, location persistence, sync limits, embedding/search metadata, deletion of orphaned chunks, and retention of documents shared by another location.

## Phase 7: Polish and Cross-Cutting Concerns

**Purpose**: Ensure actionable diagnostics, safe operation, and documentation consistency.

- [X] T018 Implement actionable, secret-redacted logging in `weather_client.py`, `app.py`, `weather_embeddings.py`, `notebooks/ingest_weather_embeddings.py`, and `lakebase.py`; log operation, failure category, and safe location/document IDs for sync, search, embedding, saved-location creation/removal, and database failures without logging credentials, OAuth tokens, DSNs, headers, raw provider payloads, or SQL parameter values.
- [X] T019 Review `sql/weather_schema.sql`, `sql/migrations/001_saved_location_associations.sql`, `specs/001-weather-vector-search/contracts/weather-api.yaml`, `specs/001-weather-vector-search/contracts/frontend.md`, `specs/001-weather-vector-search/quickstart.md`, `specs/001-weather-vector-search/deployment.md`, and `README_WEATHER.md` against the implemented routes, shared-document cleanup, grants, and search fields; correct any mismatch.

## Dependencies and Execution Order

```mermaid
graph LR
    Setup[Phase 1 Setup] --> Foundation[Phase 2 Foundation]
    Foundation --> US1[US1 Saved locations and sync]
    US1 --> US2[US2 Embedding]
    US2 --> US3[US3 Search]
    US3 --> US4[US4 Local and Databricks]
    US4 --> Polish[Phase 7 Polish]
```

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; creates the Python project and local container
  stack.
- **Foundational (Phase 2)**: Depends on Setup; schema, upgrade migration, and connection
  helper block all user stories.
- **US1 (Phase 3)**: Depends on Foundation; persists locations and produces associated
  source documents.
- **US2 (Phase 4)**: Depends on US1 documents and database operations.
- **US3 (Phase 5)**: Depends on US2's compatible embeddings and US1's saved-location UI.
- **US4 (Phase 6)**: Depends on US1–US3 for the complete local and deployed workflow.
- **Polish (Phase 7)**: Depends on the desired stories being complete.

### Parallel Opportunities

- T001 and T002 can run in parallel; T003 follows them.
- T004 and T006 can run in parallel; T005 follows T004.
- In US3, T013 and T014 can run in parallel after US2 because they modify the API and
  frontend respectively.
- In US4, T015 and T016 can run in parallel; T017 requires the full workflow.
- User stories are sequentially dependent: US2 requires US1, US3 requires US2, and the
  complete US4 demonstration requires US1–US3.

## Parallel Examples by User Story

**US1**: No parallel tasks within the story; database operations, routes, sync, and UI
are integrated in dependency order.

**US2**:

```text
Task T011: Chunking and model encoding in weather_embeddings.py
Then:
Task T012: Batch persistence and reconciliation in notebooks/ingest_weather_embeddings.py
```

**US3**:

```text
Task T013: Search endpoint in app.py
Task T014: Search UI in templates/index.html, static/weather.js, and static/weather.css
```

**US4**:

```text
Task T015: Databricks runtime configuration in app.yaml
Task T016: User/deployment documentation in README_WEATHER.md
Then:
Task T017: Manual walkthrough using quickstart.md and deployment.md
```

## Implementation Strategy

### MVP First

1. Complete Setup and Foundation.
2. Complete US1 to persist/manage locations, synchronize weather, and remove orphaned
   documents and chunks while preserving shared source documents.
3. Stop for US1's manual acceptance walkthrough; this is the MVP increment.
4. Complete US2 and US3 to deliver the full sync → embed → search learning flow.
5. Complete US4 and Polish for local and Databricks delivery.

### Task Counts

- Setup: 3
- Foundational: 3
- US1: 4
- US2: 2
- US3: 2
- US4: 3
- Polish: 2
- **Total: 19 tasks**
