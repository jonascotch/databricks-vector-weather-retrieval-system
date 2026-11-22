# Implementation Plan: Weather Vector Search

**Branch**: `001-weather-vector-search` (Spec Kit feature ID; this folder-backed workspace has no Git branch) | **Date**: 2026-11-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-weather-vector-search/spec.md`

## Summary

Build a Flask application with a simple browser interface and a separate batch process
that harvest location-specific NWS alert and forecast narratives, normalize and persist
them, create 384-dimensional `all-MiniLM-L6-v2` embeddings, and retrieve relevant chunks
by cosine distance. Provide two execution targets: a Docker Compose stack with local
PostgreSQL/pgvector for development without Databricks, and a Databricks App backed by
Lakebase for the intended deployment. The interface lets users add locations, sync
weather, enter a semantic-search query, and review ranked results with source and
freshness metadata. Location entry uses one form with a city/state or coordinate mode;
accepted locations are saved in one shared, persistent list per app deployment, and
sync submits only that list. Removing a location removes its association to synchronized
documents and deletes documents/chunks that no remaining saved location references;
shared source documents remain available to their other locations. Search results list
all locations still associated with each match.
Each selected location is limited to 20 outbound NWS request
attempts per sync, including retries. City/state inputs use the U.S. Census Gazetteer
Places file to find representative coordinates; coordinate inputs go directly to NWS
`/points`. A document state prevents stale chunks from appearing in search while changed
content is re-embedded.

## Technical Context

**Language/Version**: Python 3.11 for local development; use the Python runtime supported
by the target Databricks Apps environment and pin compatible dependencies.

**Primary Dependencies**: Flask, Gunicorn, `requests`, `psycopg2`, `pgvector` Psycopg 2
adapter, `sentence-transformers`, `databricks-sdk` for Lakebase app credentials, Docker
Compose, and the pinned U.S. Census Gazetteer Places data file. Use Flask-served HTML,
CSS, and browser-native JavaScript for the single-page interface; no separate frontend
framework or build tool is required.

**Storage**: Local PostgreSQL with `pgvector` in Compose for development; Lakebase
PostgreSQL with `pgvector` for Databricks deployment. Both targets use the same schema
SQL, persistent saved-location records and location/document associations, `jsonb`
source payloads, `vector(384)` embeddings, relational document/chunk records, and an HNSW
cosine index.

**Automated Testing**: Not required for this learning exercise. Specification FR-027
documents the scoped exception to Constitution Principle V, with end-to-end manual
demonstrations in `quickstart.md` as the compensating measure.

**Target Platform**: Docker Compose for local development and Databricks Apps with
Lakebase Autoscaling for the course deployment. Databricks workspace authentication and
app permissions protect the deployed service; it is not publicly accessible.

**Project Type**: Flask web application with a browser interface and a standalone Python
batch embedding process.

**Performance Goals**: No latency or availability SLO is specified. Enforce the
50-document default per-request document limit, the caller-supplied total-document
limit, the `top_k` range of 1–20, and the 20-request-per-location sync budget. No fixed
maximum number of locations per sync is a requirement.

**Constraints**: NWS requests require a descriptive `User-Agent`; use the location point
alert filter, follow forecast URLs returned by `/points`, respect NWS cache/rate guidance,
and pace requests to the NWS Alerts service at no more than one request every 30 seconds
across the process. Cap each location at 20 outbound NWS request attempts per sync,
counting retries. Account for retries at the HTTP transport boundary so automatic retry
behavior cannot exceed the budget. Use Python's standard `logging` module to record
actionable operation and failure context with safe identifiers, while redacting or
omitting credentials, OAuth tokens, DSNs, request headers, raw provider payloads, and SQL
parameter values. Do not use Spark JDBC for writes. Load one matching embedding model
per serving process.
Local Compose must work without Databricks credentials and bind its published app port
to localhost. Databricks Apps must use its Lakebase resource and managed service-principal
identity, with OAuth credentials refreshed for new Psycopg 2 connections. Do not expose
an unauthenticated endpoint publicly. Persist saved locations and document/location
associations. Add, remove, and orphan-document cleanup must complete atomically; deleting
one location must not delete source documents still associated with another saved
location.

**Scale/Scope**: U.S. locations only, location-specific alerts, daily/hourly gridpoint
forecasts, 50 documents per sync by default, and one current document per alert identity
or forecast location/type/period. There is no fixed maximum location count per sync;
location counts in examples are illustrative, not a capacity requirement. No forecast
history, multi-source ingestion, scheduled sync, generated summaries, or performance
benchmark is required.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / constraint | Status | Evidence |
|---|---|---|
| Source fidelity and provenance | PASS | Normalized records retain source payload, location, source type, and source/forecast times. |
| Reproducible, idempotent ingestion | PASS | Stable alert/forecast identities, deterministic chunking, location/document associations, and transactional cleanup of unreferenced documents are specified. |
| Retrieval integrity | PASS | Search uses matching 384-dimension vectors, filters non-current or unassociated documents, orders by cosine distance, and returns source/freshness metadata with every associated saved location. |
| Contract-driven API | PASS | Saved-location CRUD, sync/search requests, response, validation, limits, and error shapes are specified in `contracts/weather-api.yaml`; the shared location list, deletion behavior, and search display are specified in `contracts/frontend.md`. |
| Testability and observability | DOCUMENTED EXCEPTION | Spec FR-027 waives automated testing for this learning exercise; the scope, rationale, and manual end-to-end demonstration are documented. Task T019 specifies actionable failure context and secret-redacted logs across sync, search, embedding, and database operations. |
| Data/platform constraints | PASS | Lakebase, pgvector, Sentence Transformers, and psycopg2 are retained; Spark JDBC is excluded. |
| Local and deployment security boundary | PASS | Local Compose is loopback-only; Databricks Apps uses workspace SSO and app permissions, with no anonymous/public access. |

The only exception is the explicitly documented Constitution Principle V test waiver in
FR-027. No other constitution violations require an exception.

## Project Structure

### Documentation (this feature)

```text
specs/001-weather-vector-search/
├── plan.md
├── research.md
├── data-model.md
├── deployment.md
├── quickstart.md
├── contracts/
│   ├── weather-api.yaml
│   └── frontend.md
└── tasks.md                 # Created by /speckit-tasks, not by this command
```

### Source Code (repository root)

```text
app.py                              # Flask UI, saved-location, sync, and search routes
weather_client.py                  # Census place lookup and NWS client
lakebase.py                        # Database connection helper for local and Lakebase
weather_embeddings.py              # Chunking, model loading, and reconciliation
requirements.txt
.gitignore                          # Excludes local Gazetteer data and .env
.env.example                       # Local development variable names/placeholders
.dockerignore
Dockerfile
docker-compose.yml
app.yaml                            # Databricks Apps runtime configuration
templates/
└── index.html                      # Location and semantic-search interface
static/
├── weather.js                      # Browser interactions with the REST API
└── weather.css                     # Minimal responsive styling
sql/
├── weather_schema.sql             # Shared, idempotent local/Lakebase DDL
└── migrations/
    └── 001_saved_location_associations.sql  # Upgrade existing database data
notebooks/
└── ingest_weather_embeddings.py   # Standalone psycopg2 batch entry point
README_WEATHER.md
data/
└── us_places.txt                  # Downloaded Census Gazetteer; not committed
```

**Structure Decision**: The workspace already has a root-level Flask service, NWS
client, database/schema helpers, local Compose setup, and initial location-sync
interface. Extend those assets with the remaining embedding, search, deployment, and
documentation work rather than introducing a larger package hierarchy. Serve
`templates/index.html` at `/` and use browser-native JavaScript
to call the documented REST endpoints; keep frontend assets local and avoid an additional
framework or build system. The page has a single location-entry form with an accessible
city/state-versus-coordinates mode selector. Adding a valid value appends a read-only
display of that location to the shared, persisted selected list; each item has a remove
action. Sync submits only saved locations. Removing an item deletes its location
associations and any weather documents/chunks no other saved location references, in
one transaction; shared documents remain linked to the locations that still use them.
Keep the shared DDL in `sql/weather_schema.sql`; use it for both the Compose
database and Lakebase. Mount the script read-only at
`/docker-entrypoint-initdb.d/10-weather_schema.sql` so a fresh Compose database initializes the schema
automatically; reapply the idempotent script explicitly for an already-initialized
volume only to create missing objects; use a versioned migration to alter existing
objects and backfill location associations. The migration keeps legacy document
location columns but relaxes their `NOT NULL` constraints, along with legacy grid
columns, because the normalized model stores locations only in the saved-location and
association tables. Compose publishes the Flask port only on `127.0.0.1`,
keeps PostgreSQL on the internal service network, waits for database health, and stores
database files in a named volume. Databricks Apps uses a root `app.yaml`, Gunicorn,
Lakebase database resource, and the app service principal. Keep the downloaded Gazetteer
file and local `.env` out of source control.

## Post-Design Constitution Check

| Gate | Status | Design evidence |
|---|---|---|
| Source fidelity and provenance | PASS | `weather_documents` retain stable NWS identity, raw payloads, and timestamps; the association table traces each document to all saved locations using it. |
| Reproducible, idempotent ingestion | PASS | Unique logical IDs, content hashes, versioned model metadata, transactional chunk reconciliation, and atomic location cleanup prevent duplicates, orphans, and stale results. |
| Retrieval integrity | PASS | HNSW uses `vector_cosine_ops`; query uses `<=>`; only current-document chunks with active location associations are eligible, and each match includes all associated locations and source/freshness metadata. |
| Contract-driven API | PASS | OpenAPI defines saved-location CRUD plus sync/search endpoints, validation, per-location request-budget status, deletion outcomes, and search metadata; the frontend contract defines persistent list behavior, deletion, and result display. |
| Testability and observability | DOCUMENTED EXCEPTION | FR-027 records the learning-exercise test waiver, its scope and rationale, and manual demonstration as the compensating measure; T019 specifies actionable, secret-redacted logs for operational failures. |
| Platform, quality, and security constraints | PASS | The production/course target remains Lakebase with psycopg2 writes; local PostgreSQL is a development equivalent. Compose is loopback-only, while Databricks Apps enforces workspace SSO and app permissions. |

Retrieval results include associated locations, source type, and applicable timestamps.
The browser interface shows them, and sync responses report when a location reaches the
20-request budget.
No gate failures remain beyond the documented, user-directed testing exception.
