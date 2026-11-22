# Feature Specification: Weather Vector Search

**Feature Branch**: `not applicable (folder-backed workspace; no Git branch)`

**Created**: 2026-11-22

**Status**: Draft

**Input**: User description: "Adapt the specification for a learning exercise where automated testing is not required; include a simple frontend for adding locations and entering semantic-search queries; cap weather API requests at 20 per location; and remove test requirements."

## Clarifications

### Session 2026-11-22

- Q: When a synchronized weather document’s narrative changes, how should its existing search passages and vectors be handled? → A: Reconcile by original document ID: keep vectors for unchanged chunk text, delete obsolete chunks, add new or changed chunks, and exclude all existing vectors from search after a narrative change until reconciliation completes successfully.
- Q: What access rule should apply to the weather sync and search endpoints? → A: Keep local endpoints unauthenticated but loopback-only; for Databricks deployment, rely on workspace SSO and app permissions rather than endpoint-level authentication. Anonymous/public exposure and production-grade per-user authorization are out of scope.
- Q: For each selected location, should synchronization include only alerts affecting that location or all active alerts in its state? → A: Include only alerts affecting the specific selected location.
- Q: When an NWS alert has both a description and an instruction, which text should be included in the searchable narrative? → A: Include both fields with clear labels so search can retrieve hazard explanations and recommended actions without conflating them.
- Q: When the same location and forecast period are synchronized again, should the system replace the existing forecast document or keep each version as history? → A: Upsert one current document per location, forecast type, and forecast period; do not retain historical versions.
- Q: If a weather alert is associated with two saved locations and one is removed, should its document and chunks remain for the other location? → A: Remove the deleted location's association and retain the shared document and chunks while another saved location still references them; delete them when no saved locations reference them.

## User Scenarios & Testing *(mandatory)*

This is a learning exercise, so a dedicated automated test suite, test fixtures, and
test infrastructure are not required. The acceptance scenarios below describe expected
behavior for implementation and demonstration; they do not require automated tests.

### User Story 1 - Sync Weather for Selected Locations (Priority: P1)

A user adds one or more U.S. locations in the frontend and starts a sync. The page shows
the outcome and document count for each location. Locations may be entered as city/state
names or latitude/longitude coordinates. A single location-entry form lets the user
choose either input mode; each added location appears in a separate selected-location
list and can be removed before synchronization. The list is saved for the application
deployment, so the same locations are available to all authorized users across visits
and browser sessions.

**Why this priority**: Without current, location-specific source documents, the rest of
the pipeline has no weather content to search.

**Demonstration**: Add a city/state location and a coordinate location in the page, run
sync, and review the reported outcomes.

**Acceptance Scenarios**:

1. **Given** a valid city/state location, **When** the user synchronizes weather,
   **Then** the system resolves the location and stores its available active alerts and
   forecast narratives with source and time metadata.
2. **Given** a valid latitude/longitude pair, **When** the user synchronizes weather,
   **Then** the system resolves the corresponding NWS grid point and stores its
   available documents, including only alerts whose official geography covers that
   location.
3. **Given** an unchanged source record is synchronized again, **When** synchronization
   completes, **Then** the record is updated or recognized without creating a duplicate.
4. **Given** a forecast for the same location and period has changed, **When** it is
   synchronized again, **Then** the current forecast record is updated rather than
   creating a historical duplicate, and its vectors are marked stale.
5. **Given** a request contains multiple locations and one cannot be resolved,
   **When** synchronization completes, **Then** valid locations are still processed and
   the response identifies the failed location.
6. **Given** the user adds locations in either input mode, **When** an entry is accepted,
   **Then** it appears in the selected-location list, can be removed there, and only the
   remaining listed locations are submitted for synchronization.
7. **Given** locations have been saved by an authorized user, **When** any authorized
   user opens or reloads the frontend, **Then** the current shared location list is
   displayed.
8. **Given** an authorized user adds or removes a location, **When** the change
   succeeds, **Then** it remains reflected for other users and later visits to the same
   application deployment. When removal succeeds, that location is no longer associated
   with its weather documents. Documents and chunks no longer associated with any saved
   location are deleted; shared documents remain available for other saved locations.
   If removal fails, the location and its data remain available and the frontend reports
   the failure.

---

### User Story 2 - Prepare Weather Narratives for Search (Priority: P1)

A user can process newly synchronized documents into searchable text chunks while
retaining the relationship between each chunk, its source document, and its location.

**Why this priority**: Semantic retrieval requires vectors that remain traceable to the
weather narratives and provenance users need to evaluate results.

**Demonstration**: Run the documented embedding process after synchronization and confirm
that the resulting documents are available to semantic search.

**Acceptance Scenarios**:

1. **Given** an unembedded short document, **When** the user runs the embedding process,
   **Then** the document is represented by a searchable chunk linked to its source.
2. **Given** a document longer than the configured chunk size, **When** it is processed,
   **Then** its chunks cover the narrative with the configured overlap and stable ordering.
3. **Given** an already processed document with unchanged content and configuration,
   **When** it is processed again, **Then** duplicate embedding records are not created.
4. **Given** a document whose narrative has changed, **When** its chunks are reconciled,
   **Then** unchanged chunk vectors are retained, obsolete chunks are removed, and new
   or changed chunks are added before the document becomes searchable again.

---

### User Story 3 - Search Weather Narratives (Priority: P1)

A user enters a natural-language question in the frontend and receives the most relevant
weather narrative passages, with the associated saved location or locations, source
type, freshness details, and similarity information.

**Why this priority**: Search is the user-facing value of the ingestion and embedding
pipeline.

**Demonstration**: Enter a natural-language question on the page and review the ranked
passages and their source and freshness details.

**Acceptance Scenarios**:

1. **Given** searchable documents exist, **When** the user submits a valid query,
   **Then** the page displays the requested highest-relevance passages with their
   associated saved location or locations, headline, source type, available timestamps,
   chunk text, and similarity score.
2. **Given** no embeddings exist, **When** the user searches, **Then** the system returns
   an empty result set with a clear response rather than an internal error.
3. **Given** a missing, blank, or malformed query, **When** the user searches,
   **Then** the system returns a clear client error.
4. **Given** a requested result count outside the supported range, **When** the user
   searches, **Then** the system applies the documented bound.

---

### User Story 4 - Run Locally and Deploy to Databricks (Priority: P1)

A developer can run the full pipeline locally without Databricks, then
deploy the same Flask service to Databricks with Lakebase as its persistent database.

**Why this priority**: Local development makes the homework pipeline easy to explore,
while Databricks deployment is the intended final exercise outcome.

**Demonstration**: Start the app and local database with Docker Compose, complete the
sync/embed/search workflow through the page, then follow the deployment guide to create
the Lakebase schema and deploy the app for an authorized Databricks workspace user.

**Acceptance Scenarios**:

1. **Given** Docker Compose is installed and no Databricks credentials are configured,
   **When** the developer starts the local stack, **Then** the Flask API and local
   pgvector database become ready and the full pipeline can be exercised.
2. **Given** documents have been stored locally, **When** the database container is
   recreated without deleting its named volume, **Then** the documents remain available.
3. **Given** a Databricks Lakebase database and app have been provisioned, **When** the
   developer runs the documented SQL setup and deploys the app, **Then** an authorized
   Databricks workspace user can sync and search weather documents.

### Edge Cases

- A city/state name cannot be resolved, or a coordinate does not map to an NWS grid point.
- The weather API returns no active alerts or no forecast narrative for a location.
- A source record lacks optional text or timestamps; the record is skipped or represented
  without inventing values, and the outcome is reported.
- An alert has a description, an instruction, both, or neither; available fields are
  included with their meaning preserved, and an alert without usable narrative is not
  embedded.
- A weather API request fails or is rate-limited while other locations remain processable.
- A location reaches its budget of 20 outbound weather API request attempts during one
  sync; the system makes no further requests for that location and reports the incomplete
  sync outcome.
- Repeated synchronization returns a record whose narrative or metadata has changed.
- A changed document's vectors are excluded from search until its refreshed chunk set is
  reconciled successfully; a failed refresh does not restore stale vectors.
- A document is shorter than one chunk, exactly one chunk, or spans multiple overlapping
  chunks.
- The saved-location list cannot be loaded, a location change cannot be persisted, or
  weather data for a location cannot be deleted; the frontend reports the failure and
  does not present an unsuccessful change as complete.
- A document exists but has no embedding, or its stored embedding model/dimension differs
  from the active query model.
- The search query is valid but no indexed content is relevant.
- `top_k` is omitted, non-numeric, below the minimum, or above the maximum.
- The local database is still starting when the app launches; the app waits for database
  health and handles later database outages.
- A local Compose database container is recreated; its named data volume preserves
  records unless explicitly removed.
- The local and Lakebase PostgreSQL versions differ; schema setup fails clearly if the
  required pgvector extension or HNSW operator class is unavailable.
- A Databricks user is not granted app access; the platform denies access before the
  unauthenticated Flask routes are reached.
- A user has not added any locations or submits an empty search query; the page explains
  the required input and does not start the corresponding operation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept a list of U.S. locations as city/state strings,
  `{ "lat": number, "lon": number }` objects, or a mixture of both.
- **FR-002**: The system MUST resolve each location to the NWS grid point needed to
  retrieve location-specific weather information. City/state inputs MUST be resolved
  through a documented geocoding source; coordinate inputs MUST be passed through
  location validation.
- **FR-003**: The system MUST harvest active weather alerts affecting each requested
  location and gridpoint forecast narratives, including the `detailedForecast` text from
  daily and hourly forecasts. State-wide alerts unrelated to the requested point and
  separate Area Forecast Discussion products are not included in this feature.
- **FR-004**: The system MUST normalize each source item into a document record with a
  stable deduplication identifier, association with one or more saved locations,
  source type, headline or event,
  embeddable narrative text, available issue/effective timestamps, original payload for
  provenance, and synchronization time. Alert identifiers MUST use the provider's stable
  alert ID. Forecast identifiers MUST be based on location, daily/hourly forecast type,
  and forecast period start/end so a refreshed period updates the current document.
  Alert narratives MUST include both `description` and `instruction` when available,
  clearly labeled; forecast narratives MUST use the gridpoint `detailedForecast` text.
- **FR-005**: The system MUST store normalized documents in a Lakebase table named
  `weather_documents`, following the project's `get_connection()` context-manager
  pattern and using `psycopg2` with `RealDictCursor`.
- **FR-006**: The system MUST provide `POST /weather/sync`, accepting a list of saved
  locations and optional `limit`. The default limit MUST be 50 documents per request;
  the limit MUST cap the total documents synchronized in that request.
- **FR-006a**: A single sync operation MUST make no more than 20 outbound weather API
  request attempts for each selected location. This count MUST include retries and all
  weather API endpoints called for that location. Once its budget is exhausted, the
  system MUST make no further weather API requests for that location and MUST report
  that its sync was incomplete. A failure at one location MUST NOT consume another
  location's request budget.
- **FR-007**: The sync operation MUST upsert documents by stable identifier and return
  the number synchronized. For mixed-success location requests, it MUST process valid
  locations and report failures by location. If a document's narrative changes, the
  system MUST mark its existing vectors stale and exclude them from search until the
  document's chunks have been reconciled successfully.
- **FR-008**: The system MUST provide a standalone Python batch script that reads
  documents without current embeddings through the existing connection helper, chunks
  their narrative text, and preserves each chunk's document relationship and order. For
  changed documents, it MUST reconcile vectors by document ID: retain unchanged chunk
  vectors, delete obsolete chunks, add new or changed chunks, and make the document
  searchable only after the reconciled set is complete.
- **FR-009**: The initial chunking configuration MUST use a sliding window of 800
  characters with 100 characters of overlap. Each shorter narrative MUST produce at
  least one chunk.
- **FR-010**: The embedding process MUST use
  `sentence-transformers/all-MiniLM-L6-v2` with 384-dimensional vectors, consistent
  with the existing news embedding model and dimensionality.
- **FR-011**: Database setup MUST create `weather_documents` and `weather_embeddings`,
  including a foreign-key relationship that prevents orphaned embedding records. The
  embedding table MUST include a 384-dimensional vector, chunk index and text, model
  name, and creation time, and MUST have a vector index suitable for cosine-distance
  retrieval. An idempotent SQL schema script MUST create the `vector` extension, tables,
  constraints, and indexes for both local PostgreSQL/pgvector and Lakebase.
- **FR-012**: The embedding process MUST write to Lakebase through `psycopg2`, using
  `execute_values` or batched conflict-safe inserts, bounded batches, transactions, and
  an explicit cast to the database vector type. Spark JDBC MUST NOT be used for the
  Lakebase write path.
- **FR-013**: The system MUST provide `POST /weather/search`, accepting a non-empty
  `query` and optional `top_k`. The default `top_k` MUST be 5 and valid values MUST be
  bounded to 1 through 20.
- **FR-014**: Search MUST encode the query using the same model and dimensions as stored
  document vectors, with the model loaded once for the application rather than once per
  request. It MUST rank results with pgvector's `<=>` cosine-distance operator. Returned
  similarity MUST be calculated as one minus cosine distance.
- **FR-015**: Each search result MUST include the source document identifier, all saved
  locations still associated with that document, source type, headline, matched chunk
  text, similarity score, synchronization time, and applicable issue/effective or
  forecast-period timestamps. Unavailable or inapplicable timestamps MUST be represented
  as null.
- **FR-016**: Search MUST return an empty result collection when no embeddings exist.
  Missing, blank, or malformed queries MUST receive a clear validation response.
- **FR-017**: The system MUST document the selected weather source, schema and chunking
  decisions, embedding model and dimensions, Docker Compose local run instructions,
  Databricks deployment steps, SQL schema setup, and known limitations in
  `README_WEATHER.md` or an equivalent main README section.
- **FR-018**: Credentials and connection details MUST use the project's established
  secret or environment configuration and MUST NOT be committed to source control.
- **FR-019**: Weather API access MUST comply with the provider's published access terms
  and rate limits.
- **FR-020**: The local Compose API MUST bind to localhost for trusted course use.
  Databricks deployment MUST rely on Databricks workspace authentication and app
  permissions, granting app use only to authorized course users. Anonymous/public access
  and production-grade per-user authorization are outside this feature's scope.
- **FR-021**: Docker Compose MUST start the Flask API and a local PostgreSQL service with
  `pgvector`, without requiring Databricks deployment or credentials. The database MUST
  use a health check and a named persistent volume.
- **FR-022**: The application MUST be deployable as a Databricks App connected to
  Lakebase through a Databricks app database resource. Its deployment configuration MUST
  bind to the platform-provided app port and use the app's managed database identity.
- **FR-023**: The deployment guide MUST provide the SQL statements to enable pgvector
  and create the weather document and embedding tables, constraints, grants, and search
  indexes in Lakebase.
- **FR-024**: The system MUST provide a simple browser frontend that lets users add and
  remove multiple U.S. locations as city/state names or latitude/longitude pairs. The
  frontend MUST use a single location-entry form that lets the user choose city/state
  or coordinate input, then add a valid location to a separate selected-location list.
  Each listed location MUST be removable, and synchronization MUST submit only the
  locations currently in that list. The frontend MUST also start synchronization and
  show per-location outcomes and synchronized document counts.
- **FR-025**: The frontend MUST provide a text input for semantic-search queries and
  display ranked matches with every saved location still associated with each match,
  source type, headline, chunk text, similarity, and applicable freshness timestamps.
- **FR-026**: The frontend MUST present clear validation or operational feedback when
  location input is invalid, synchronization fails, search input is empty, or no
  searchable results exist. It MUST also clearly report failures to load or persist the
  shared saved-location list or delete location data, and MUST NOT present an
  unsuccessful location change as complete.
- **FR-027**: For this learning exercise only, the automated-testing requirement in
  Constitution Principle V is waived because automated testing is not an assessed
  deliverable. No automated test suites, test fixtures, or dedicated test infrastructure
  are required. As the compensating measure, the implementation and documentation MUST
  support manually demonstrating the end-to-end user flows described in this
  specification.
- **FR-028**: The system MUST persist one shared saved-location list per application
  deployment for all authorized users. The frontend MUST load the current list whenever
  it is opened or refreshed, retain additions across visits and browser sessions, and
  persist removals so removed locations are absent from later views and synchronization
  requests. Changes made by one authorized user MUST be visible to other authorized
  users of that deployment. A successful removal MUST remove that location's
  associations with its weather documents. The system MUST delete documents and their
  chunks and vectors when no saved locations remain associated with them; a document
  still associated with another saved location MUST remain available only for the
  remaining locations and search results MUST include only its remaining saved
  locations. Removed locations MUST NOT appear in search results or future sync
  requests. Removal MUST be all-or-nothing: if the associations and any now-
  unreferenced data cannot be removed, the saved location and its associations remain
  available, the frontend reports the failure, and the removal can be retried.

### Key Entities *(include if feature involves data)*

- **Weather Location**: A U.S. city/state name or coordinate pair, resolved to a
  location and NWS grid point.
- **Weather Document**: A normalized alert or forecast narrative with stable identity,
  source type, location, timestamps, raw source payload, synchronization time, and
  current or pending embedding state. Forecast documents also retain daily/hourly type
  and period start/end.
- **Weather Embedding**: An ordered text chunk and its vector, linked to one weather
  document and identified with the model used to create it.
- **Saved Location**: A city/state name or coordinate pair in the shared list for one
  application deployment, available for synchronization until an authorized user
  removes it. Successful removal also deletes its synchronized weather documents and
  associated chunks and vectors.
- **Search Match**: A document chunk returned for a query with all saved locations
  still associated with its source document, source type, headline, similarity, and
  source/freshness timestamps.
- **Frontend Session**: A user's current synchronization outcomes, search query, and
  results during use of the simple browser interface.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: During a walkthrough, users can trace every returned weather item to its
  selected location, source type, and originating source record.
- **SC-002**: Repeating synchronization for unchanged source records creates zero
  duplicate document identities.
- **SC-003**: Users can trace 100% of returned passages to the originating weather item
  and location, including when an item is divided into multiple passages.
- **SC-004**: A user can add locations, synchronize them, enter a search query, and view
  ranked results through the browser interface without constructing API requests manually.
- **SC-005**: Each selected location makes no more than 20 outbound weather API request
  attempts during one synchronization operation, including retries.
- **SC-006**: A developer unfamiliar with the feature can follow the documentation to
  sync, embed, and search a sample location without undocumented manual steps.
- **SC-007**: A developer without Databricks credentials can start the local Compose
  environment and complete a sync-to-search run, with documents surviving a database
  container recreation that retains its named volume.
- **SC-008**: An authorized Databricks workspace user can complete the documented
  deployment flow and run sync-to-search against Lakebase after the included schema SQL
  has been applied.
- **SC-009**: 100% of locations successfully added to the shared list remain visible to
  authorized users after a page reload or a later browser session, until removed.
- **SC-010**: After a successful add or removal, another authorized user sees the same
  updated location list on their next page load or refresh.
- **SC-011**: After a successful removal, zero weather documents or associated chunks
  remain associated with that location. Documents shared with another saved location
  remain searchable for that location; removed locations never appear in search results.

## Assumptions

- The first release uses the National Weather Service API for U.S. weather records and
  does not combine weather sources.
- Local development uses a Compose-managed PostgreSQL/pgvector database and does not
  require Databricks credentials. Databricks is the intended deployment target, using a
  Lakebase-backed Databricks App restricted to authenticated course users. Anonymous
  public exposure is not in scope.
- “Forecast discussions” means the NWS gridpoint `detailedForecast` narratives from
  daily and hourly forecast products. Separate Area Forecast Discussion products are
  outside this feature's scope.
- City/state inputs require a public geocoding source before they can be resolved through
  NWS grid points. The plan selects a version-pinned U.S. Census Gazetteer Places file;
  coordinate inputs do not require geocoding.
- The sync limit defaults to 50 and caps the total records across all requested locations.
- No fixed maximum number of locations is specified; any location count used in
  examples is illustrative and does not define a capacity requirement.
- The maximum of 20 weather API request attempts is enforced independently for each
  location during each sync operation; retries count toward that maximum.
- Only the latest synchronized version for a location, daily/hourly forecast type, and
  forecast period is kept; historical forecast versions are not part of this feature.
- A valid search with `top_k` omitted requests five results. Numeric values below 1 or
  above 20 are clamped to the supported range; non-numeric values are rejected.
- If an individual location fails during a multi-location sync, successful locations
  still complete and the response reports the failed locations.
- Saved locations are shared among authorized users of the same application deployment;
  local and Databricks deployments maintain separate lists.
- Removing a saved location deletes its associations to weather documents and deletes
  documents and chunks/vectors no longer referenced by any saved location. Data shared
  with another saved location is retained for that location; deletion failure leaves the
  saved location and its associations available for retry.
- The optional GET search with generated summaries, scheduled sync jobs, additional
  weather sources, source-type filters, and index-performance benchmarks are stretch
  goals and are not required for the initial release.
- The frontend is a simple course-use interface for selecting locations, triggering
  sync, entering a semantic-search query, and reviewing results; advanced account
  management and production-grade interaction design are out of scope.
