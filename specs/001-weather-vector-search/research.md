# Research: Weather Vector Search

## NWS location and forecast retrieval

**Decision**: For coordinates, call NWS `/points/{latitude},{longitude}` and follow the
returned `forecast` and `forecastHourly` URLs. For active alerts, use the point-specific
alerts query so documents apply to the requested place. Keep the NWS feature ID for
alerts and the grid point/product/valid-period tuple for forecast identity.

**Rationale**: NWS documents `/points` as the mapping from a geographic coordinate to
office/grid metadata and includes canonical forecast URLs. Its Alerts Web Service
documents point-scoped alert queries. Following returned links avoids reconstructing
forecast paths and supports grid changes.

**Alternatives considered**: State-wide alert queries would include hazards unrelated to
the selected point, so they are excluded. Constructing forecast URLs from grid fields is
unnecessary when NWS returns the links.

**Operational findings**:

- Send a descriptive `User-Agent` with an application identifier and contact address;
  the NWS API does not require an API key.
- NWS says typical use is allowed but does not state a universal numeric rate limit.
  The Alerts service recommends no more than one request every 30 seconds. Honor cache
  headers, avoid cache-busting parameters, deduplicate identical location requests, and
  pace alert requests accordingly. The feature additionally caps outbound NWS request
  attempts at 20 per location per sync, counting retries. Request attempts must be
  accounted for at the HTTP boundary so retry behavior cannot exceed the cap; stop
  fetching for that location at the limit and report a partial outcome.
- NWS documents coordinate precision limitations; normalize request coordinates to no
  more than four decimal places before `/points`.
- An empty `features` list from a point alert query is a valid no-alert result.
- NWS hourly forecast periods may have an empty `detailedForecast`. In this feature,
  do not invent forecast text; skip a period without the required narrative and record
  that it supplied no embeddable text. Daily and hourly detail remains the specified
  forecast field.

Sources: [NWS API documentation](https://www.weather.gov/documentation/services-web-api),
[NWS Alerts Web Service](https://www.weather.gov/documentation/services-web-alerts),
[NWS OpenAPI](https://api.weather.gov/openapi.yaml),
[NWS API FAQ](https://weather-gov.github.io/api/general-faqs).

## City/state geocoding

**Decision**: Use a locally downloaded, version-pinned U.S. Census Gazetteer Places file
to resolve normalized city name plus state abbreviation to representative coordinates.
Require an unambiguous exact place/state match; report no-match or ambiguous input and
ask the caller to use coordinates.

**Rationale**: NWS `/points` requires coordinates. The Census Gazetteer contains named
places and representative latitude/longitude coordinates, works without a per-request
external geocoding call, and fits the U.S.-only scope. Coordinate inputs remain the
precise option.

**Alternatives considered**: The Census Geocoder API is address-oriented and requires a
street component; it is not a direct city/state lookup. Public Nominatim supports place
search but imposes a strict one-request-per-second policy, attribution and caching rules,
making a local Gazetteer a better fit for batch sync.

Sources: [Census Gazetteer files](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html),
[2026 Census Gazetteer files](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2026.html),
[Census Geocoder API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html),
[Nominatim Search API](https://nominatim.org/release-docs/latest/api/Search/),
[Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/).

## Lakebase, psycopg2, and pgvector

**Decision**: Keep normalized documents in `weather_documents` and chunk vectors in
`weather_embeddings`, with a foreign key and unique `(document_id, chunk_index)` key.
Use `vector(384)`, register pgvector's Psycopg 2 adapter, and bind query vectors as
parameters. Create an HNSW index with `vector_cosine_ops`; retrieve with
`ORDER BY embedding <=> %s::vector LIMIT %s`.

**Rationale**: This follows the requested document/embedding separation, preserves the
source-to-chunk relationship, uses the same cosine operator for index and query, and
avoids unsafe SQL interpolation. `execute_values` provides bounded batched insertion.
Lakebase documents the standard `vector` extension for supported PostgreSQL versions.

**Changed-document reconciliation**: Store an `embedding_status` on each document:
`pending`, `current`, or `stale`. When normalized narrative text changes, mark it stale;
search joins only documents in `current` state. The batch job computes deterministic
chunks and hashes, reuses vectors for unchanged chunk text under the same model
revision, and calculates changed/new vectors before opening a short transaction. In one
transaction, remove obsolete chunks, insert changed/new chunks, and mark the document
current only after the complete expected chunk set is present. If embedding or writing
fails, the document remains pending/stale and excluded from search. This uses the
specification's single embedding table instead of adding a version-history table.

**Alternatives considered**: A revision table plus active-revision pointer is more
appropriate for long-running asynchronous rebuilds or retained history, but is
unnecessary for this small batch-oriented homework feature. In-place partial updates
without a transaction could expose stale or incomplete chunk sets and are rejected.

Sources: [Databricks Lakebase extensions](https://docs.databricks.com/aws/en/oltp/projects/extensions),
[pgvector](https://github.com/pgvector/pgvector),
[pgvector-python Psycopg 2 adapter](https://github.com/pgvector/pgvector-python),
[Psycopg 2 fast execution helpers](https://www.psycopg.org/docs/extras.html#fast-execution-helpers),
[Psycopg 2 parameter binding](https://www.psycopg.org/docs/usage.html#passing-parameters-to-sql-queries),
[PostgreSQL transactions](https://www.postgresql.org/docs/current/tutorial-transactions.html),
[PostgreSQL `ON CONFLICT`](https://www.postgresql.org/docs/current/sql-insert.html).

## Embedding model and serving

**Decision**: Use `sentence-transformers/all-MiniLM-L6-v2`, validate 384 output
dimensions, and pin an immutable model revision. Load one encoder per Flask worker
process and one per batch process; batch the corpus encoding. Keep query and document
preprocessing and model revision aligned.

**Rationale**: The model card and Sentence Transformers API describe 384-dimensional
outputs. Loading per request adds avoidable startup cost; process-level reuse is suitable
for this trusted local/course service. A pinned revision prevents silent model drift.

**Learning-exercise scope**: Automated testing is not a required deliverable. The
feature-specific exception to Constitution Principle V is recorded in spec FR-027; the
quickstart provides an end-to-end manual demonstration instead.

**Alternatives considered**: A different model would require an explicit dimension and
schema change, and is outside this feature. Multiple workers each use a separate in-memory
model copy; worker count must remain modest for local memory.

Sources: [all-MiniLM-L6-v2 model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2),
[Sentence Transformers model API](https://sbert.net/docs/package_reference/sentence_transformer/model.html),
[semantic similarity documentation](https://sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html),
[Flask application context](https://flask.palletsprojects.com/en/stable/appcontext/),
[Flask with Gunicorn](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/).

## Browser frontend

**Decision**: Serve a single HTML page from Flask, with local CSS and browser-native
JavaScript calling same-origin location-management, sync, and search endpoints. Use one
location-entry form with an accessible city/state-versus-coordinates mode selector.
After validation, the form saves a display-only item to the shared persistent
location list; sync submits only saved items. Search matches display every saved
location still associated with the source document.

**Rationale**: The requested interface needs only location entry, sync feedback, query
submission, and ranked result display. A small same-origin page meets those needs while
using the existing application and deployment boundary.

**Alternatives considered**: Keeping editable city/state and coordinate inputs in every
selected row mixes location entry with selection management and makes the active input
mode unclear; the separate form and selected list distinguish those responsibilities.
A separate SPA framework or frontend service would add dependencies and deployment
complexity without a corresponding feature requirement. Keep embedding as the documented
batch-process step between synchronization and search; adding a UI action that launches
that process is outside the current frontend scope.

**Safety and usability**: Provide labels and visible status/error messages, render source
text as text rather than HTML, and never collect credentials in the page. Local access
remains loopback-only; Databricks workspace permissions protect deployment. Server-side
validation and the per-location request budget remain authoritative; browser-side
validation is only for usability. Keep API calls same-origin and do not enable permissive
cross-origin access.

Sources: [Flask templates](https://flask.palletsprojects.com/en/stable/templating/),
[Flask static files](https://flask.palletsprojects.com/en/stable/tutorial/static/),
[Flask JavaScript patterns](https://flask.palletsprojects.com/en/stable/patterns/javascript/),
[Flask web security](https://flask.palletsprojects.com/en/stable/web-security/),
[WAI form labels](https://www.w3.org/WAI/tutorials/forms/labels/),
[WAI form notifications](https://www.w3.org/WAI/tutorials/forms/notifications/).

## Persistent shared locations and data cleanup

**Decision**: Persist saved locations in the shared application database. Keep weather
documents globally deduplicated by provider identity and relate each document to every
saved location for which it was synchronized. A location removal deletes the saved
location and its document associations in one transaction, then deletes documents with
no remaining saved-location associations; the document foreign key cascades deletion to
its chunks and vectors. Shared documents remain available to any remaining associated
locations. Return all remaining associated location labels with each search match.

**Rationale**: NWS alert identifiers are stable globally, and one alert can cover more
than one saved location. Removing one location must clean up its data without removing
shared weather evidence from another location. A transactional association cleanup
preserves referential integrity and supports the specification's all-or-nothing removal
behavior.

**Alternatives considered**: Keeping the list only in browser storage does not preserve
it across users or sessions. Deleting every document linked to a removed location would
also remove shared alerts needed by other saved locations. Duplicating globally
identified alert documents per location would undermine stable-ID deduplication.

**API decision**: Provide same-origin list, add, and delete operations for saved
locations. The frontend sync request references saved-location IDs rather than
reconstructing or resubmitting unsaved input. A failed removal rolls back and returns a
clear error; successful responses distinguish deleted orphan documents from documents
retained for other locations.

## Local Docker Compose execution

**Decision**: Provide a Flask application service and a PostgreSQL 16 service based on
`pgvector/pgvector:0.8.7-pg16-trixie`. Require the database health check before the app
starts, persist database files in a named volume, and publish the app only on
`127.0.0.1`; do not publish the database port to the host. Configure local credentials
through an ignored `.env` file. No Databricks account or credentials are required.

**Rationale**: This image includes pgvector 0.8.7, matching the Lakebase extension
version available for supported PostgreSQL versions. PostgreSQL 16 and standard pgvector
DDL provide a close local representation of the Lakebase target. A health check avoids
startup races, and the volume preserves homework data across container recreation.

**Schema decision**: Keep one idempotent SQL script for both backends. It creates the
`weather` schema, enables `vector`, creates the two tables and constraints, and adds
the B-tree and HNSW cosine indexes. Mount it into the Compose database's
`/docker-entrypoint-initdb.d` directory so it runs when a fresh named volume is
initialized; for an existing volume, apply the idempotent script explicitly with the
database owner/admin when updating the schema. Run extension creation with a database
owner/admin. The app's local connection settings and Databricks resource connection
settings remain environment-specific.

**Alternatives considered**: Running only the Flask process locally against Lakebase
would still require Databricks credentials and network access. SQLite cannot reproduce
pgvector operators/indexes. A separate local schema would risk behavior drift.

Sources: [pgvector Docker image](https://hub.docker.com/r/pgvector/pgvector),
[pgvector](https://github.com/pgvector/pgvector),
[Docker Compose startup order and health checks](https://docs.docker.com/compose/how-tos/startup-order/),
[Docker Compose volumes](https://docs.docker.com/engine/storage/volumes/).

## Databricks Apps and Lakebase deployment

**Decision**: Deploy the Flask service as a Databricks App with a Lakebase Autoscaling
database resource. Use root `app.yaml` to run Gunicorn bound to `0.0.0.0` and the
platform-provided `DATABRICKS_APP_PORT`. Connect as the app's managed service principal
using the database-resource `PG*` settings and Databricks SDK OAuth credentials. Generate
a fresh Lakebase database credential when creating each new Psycopg 2 connection; do
not persist an expiring token. Store the Lakebase endpoint identifier required by the
credential API in a `LAKEBASE_ENDPOINT` deployment variable. Apply the shared SQL DDL
as a database owner/admin, then grant the app role only the required schema/table
permissions.

**Access decision**: Use Databricks workspace authentication and app permissions:
`CAN USE` for the course cohort and `CAN MANAGE` only for maintainers. Databricks Apps
does not support anonymous/public access. The Flask endpoints remain without additional
application-level authentication for this exercise. Locally, the same routes are
available only through a loopback-bound Compose port.

**Rationale**: Databricks Apps supports Flask and supplies app runtime configuration,
workspace SSO, a dedicated app service principal, and a Lakebase resource. The app
service principal's database role is created with the database resource. Using the same
schema SQL on both targets exercises the actual pgvector queries locally before
deployment.

**Alternatives considered**: Deploying to a general-purpose container host would require
independently managing app authentication, secrets, and Lakebase networking. Using
Lakebase native password authentication would require enabling and managing a separate
password rather than using the managed app identity. Public or anonymous Databricks App
access is unsupported.

**Operational notes**:

- The database resource provides `PGAPPNAME`, `PGDATABASE`, `PGHOST`, `PGPORT`,
  `PGSSLMODE`, and `PGUSER`. Databricks injects `DATABRICKS_CLIENT_ID` and
  `DATABRICKS_CLIENT_SECRET` for app authorization; the SDK uses these through unified
  authentication.
- Lakebase OAuth tokens expire after one hour. The credential token is used as the
  PostgreSQL password. Obtaining one for each newly opened short-lived connection keeps
  the existing Psycopg 2 context-manager pattern simple and avoids stale pooled
  credentials.
- Enabling `vector` may require a Lakebase database owner/admin. Run the extension
  statement before table/index DDL and before granting the app role access.
- Confirm Lakebase and Databricks Apps availability for the workspace's cloud/region.

Sources: [Databricks Apps Lakebase resource](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/lakebase),
[Databricks Apps authorization](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth),
[Databricks Apps permissions](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/permissions),
[Databricks Apps runtime configuration](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-runtime),
[Lakebase Apps tutorial](https://docs.databricks.com/aws/en/oltp/projects/databricks-apps),
[Lakebase authentication and token rotation](https://docs.databricks.com/aws/en/oltp/projects/authentication),
[Lakebase extensions](https://docs.databricks.com/aws/en/oltp/projects/extensions).
