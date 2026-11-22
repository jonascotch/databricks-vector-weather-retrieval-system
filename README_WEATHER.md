# Weather Vector Search

A Flask application that synchronizes NWS alert and forecast narratives, stores them in
PostgreSQL/Lakebase, embeds narrative chunks with Sentence Transformers, and provides
semantic search through `pgvector`.

## Data flow

1. Add U.S. locations through the browser using `City, ST` or latitude/longitude.
   The shared saved-location list is stored in the database and is visible to authorized
   users of that deployment.
2. Synchronize saved locations. The server resolves city/state input using the pinned
   U.S. Census Gazetteer Places file, follows the NWS `/points` forecast links, and
   retrieves point-specific active alerts. Each location is limited to 20 outbound NWS
   request attempts per sync, including retries. The request uses a descriptive
   `NWS_USER_AGENT`; alert requests are paced at no more than one per 30 seconds per
   process.
3. Synchronization embeds new narrative chunks before responding, so newly
   synchronized documents are immediately searchable. The first sync after a
   cold start also downloads the encoder model and can take about a minute
   longer. The same reconciliation is available as a standalone batch for
   catch-up or scheduled runs:

   ```bash
   docker compose exec app python notebooks/ingest_weather_embeddings.py
   ```

4. Search the stored narratives from the browser. Results include source identity and
   type, all currently associated saved locations, matched text, similarity, and
   available source/freshness timestamps.

The NWS source payload is retained in `weather.weather_documents`. Stable alert IDs
deduplicate alerts globally; forecast identity is based on NWS grid, forecast type, and
period. Narrative chunks and 384-dimensional vectors are stored separately in
`weather.weather_embeddings`. The pinned encoder is
`sentence-transformers/all-MiniLM-L6-v2` at revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. Chunks use an 800-character sliding window
with 100-character overlap. Unchanged chunk vectors are reused; a document is searchable
only after its complete current chunk set is committed.

## Shared location management

Locations persist in the database across browser sessions and are shared among
authorized users of the same app deployment. Local Compose and a Databricks deployment
use separate databases and therefore separate lists.

Removing a location deletes its document associations and removes only documents with no
remaining location associations. Foreign-key cascade deletes the chunks/vectors of those
orphaned documents. A shared document and its chunks remain searchable for other saved
locations. The delete response reports removed-document, removed-chunk, and retained
shared-document counts.

## Local development

Requirements: Docker Engine with Docker Compose v2, network access to NWS and the Census
Gazetteer release, and disk space for PostgreSQL and the downloaded embedding model.

1. Copy `.env.example` to `.env`. Set a non-default local `POSTGRES_PASSWORD`,
   matching `DATABASE_URL`, and a descriptive `NWS_USER_AGENT` with a contact address.
   Never commit `.env`.
2. Download the pinned 2026 U.S. Census Gazetteer Places file to `data/us_places.txt`.
   The file is intentionally not committed or copied into the Docker image.
3. Start the stack:

   ```bash
   docker compose up --build --wait
   ```

The browser app is available at `http://127.0.0.1:5000/`. Only the Flask service is
published to localhost; PostgreSQL remains on the Compose network. A named volume
preserves database data across container recreation. PostgreSQL applies
`sql/weather_schema.sql` automatically only when the volume is initialized for the first
time.

For an existing legacy database, apply the upgrade migration rather than expecting the
bootstrap `CREATE TABLE IF NOT EXISTS` statements to change existing tables:

```bash
docker compose exec -T db sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f /docker-entrypoint-migrations/001_saved_location_associations.sql'
```

The migration runs transactionally, backfills saved locations and associations from
legacy location/coordinate/grid values, keeps existing document and embedding rows, and
relaxes legacy columns that the new document model no longer requires. Stop and resolve
any migration error before deploying the new application.

See [the quickstart](specs/001-weather-vector-search/quickstart.md) for a manual
sync → embed → search walkthrough and [the deployment guide](specs/001-weather-vector-search/deployment.md)
for Lakebase schema, grants, and Databricks App setup.

## Databricks deployment

Deploy as a Databricks App with a Lakebase resource. Replace the non-secret
`LAKEBASE_ENDPOINT` placeholder in `app.yaml` with the endpoint path for the selected
project/branch/endpoint. Configure `NWS_USER_AGENT` with an operator contact. Databricks
injects the `PG*` settings and managed app identity for the database resource; the app
requests a fresh Lakebase OAuth credential for each new connection. Do not copy
credentials or tokens into files.

Populate the shared places table used by the city autocomplete before the
first deployment (or to refresh it after a new Gazetteer release). The loader
creates `weather.weather_places` when missing and refreshes it idempotently;
run it as a database owner, and apply the deployment grants once so the app
role can read the table:

```bash
python notebooks/ingest_weather_places.py
```

Alternatively, create the empty table with `sql/weather_places.sql` and load it
with the same notebook. The Census Gazetteer release is downloaded directly, so
`data/us_places.txt` is not needed for the autocomplete flow; picked places are
saved with their Gazetteer coordinates and synchronized without the local file.

Run the shared schema or versioned migration as a database owner/admin, then grant the
app service-principal role the schema, table, and sequence privileges described in
`specs/001-weather-vector-search/deployment.md`. Use Databricks workspace authentication
and app permissions (`CAN USE` for users, `CAN MANAGE` for maintainers); the app does not
provide anonymous/public access.

## Limitations and exercise scope

- U.S. locations, current point-specific alerts, and current daily/hourly forecasts only.
- City/state lookup requires an exact, unambiguous Gazetteer match; coordinates are the
  alternative for ambiguous or absent place names.
- Embedding runs as part of synchronization; a standalone notebook remains for
  catch-up and scheduled runs.
- No forecast history, scheduled synchronization, answer generation, or performance SLO
  is included.
- Automated tests are waived for this learning exercise by spec FR-027. The manual
  quickstart and deployment walkthroughs are the required validation path.
