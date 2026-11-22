# Quickstart: Weather Vector Search

This guide demonstrates the sync → embed → search flow locally with Docker Compose.
Databricks deployment steps and Lakebase SQL setup are in
[deployment.md](deployment.md).

## Prerequisites

- Docker Engine and Docker Compose v2.
- Network access to NWS and the Census Gazetteer download.
- A descriptive NWS `User-Agent` value with a contact address.
- Sufficient local disk space for the PostgreSQL volume and the embedding model.
- The pinned U.S. Census Gazetteer Places file at `data/us_places.txt`.

No Databricks account or credentials are required for the local workflow.

## Start the local stack

From the repository root, create a local environment file from the example and set
the NWS `User-Agent` and any local database settings it requires. Do not commit `.env`.
Download the 2026 U.S. Places Gazetteer file from the
[Census Gazetteer release page](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2026.html)
to `data/us_places.txt`.

```bash
cp .env.example .env
docker compose up --build --wait
```

The app is published on `127.0.0.1`; PostgreSQL/pgvector is reachable only from the
Compose network. The database uses a named volume. On first startup, PostgreSQL applies
the shared schema SQL automatically. If the named volume was initialized before the
schema was added, apply the idempotent SQL using the local database owner. For an
existing schema whose table definitions need to change, run the documented versioned
migration; re-running `CREATE TABLE IF NOT EXISTS` does not alter existing tables.

```bash
docker compose exec db sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f /docker-entrypoint-initdb.d/10-weather_schema.sql'
```

Expected result: the `weather` schema contains `weather_saved_locations`,
`weather_documents`, `weather_location_documents`, and `weather_embeddings`, with
`vector(384)` and a cosine HNSW index.

For an existing legacy database, apply the versioned migration before using the updated
application:

```bash
docker compose exec -T db sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f /docker-entrypoint-migrations/001_saved_location_associations.sql'
```

The migration keeps legacy document columns and rows, backfills saved locations and
associations, and relaxes old `NOT NULL` constraints that conflict with the new
document model. It runs in one transaction; inspect and resolve any reported failure
before deploying the updated app.

## Sync, embed, and search through the frontend

Open the browser interface at `http://127.0.0.1:5000/`. Choose city/state or coordinate
mode in the location-entry form, enter a location, and add it to the saved-location
list. Reload the page and confirm the location remains. Add a second location and
synchronize the saved locations from the page.

Expected result: the page shows documents synchronized and an outcome for each location.
A location with no current alerts may still receive
forecast documents. No location makes more than 20 NWS request attempts during one sync,
including retries; the page identifies a partial result if the budget is reached.

Create or reconcile document chunks and vectors:

```bash
docker compose exec app python notebooks/ingest_weather_embeddings.py
```

Expected result: pending/stale documents are processed. Unchanged chunk vectors are
reused, obsolete chunks are removed, and a document becomes searchable only after its
complete chunk set is committed.

Return to the page, enter a natural-language question, and submit the search.

Expected result: up to five relevant passages appear in cosine-relevance order, with
all associated saved locations, source type, headline, chunk text, similarity,
synchronization time, and applicable issue/effective or forecast-period timestamps.
Unavailable timestamps are shown as unavailable. If no embeddings exist yet, the page
explains that there are no searchable results and points the user to the embedding step
above.

Remove a saved location that has synchronized data. Confirm it disappears from the
shared list and that weather documents and chunks used only by that location are
deleted. If a document is also associated with another saved location, confirm it
remains searchable with only the remaining location shown.

## Stop

Stop the services while retaining local data:

```bash
docker compose down
```

Removing the named volume is destructive to local database contents; do so only when
you intend to reset the local exercise data. For Databricks deployment, follow
[deployment.md](deployment.md).
