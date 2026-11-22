# Frontend Contract: Weather Vector Search

## Entry point

The Flask application serves the single-page interface at `/`. The page uses same-origin
requests to the weather REST endpoints and requires no separate frontend service,
Databricks credentials, or build step in local development.

## Location synchronization

- On page load, the frontend requests `GET /weather/locations` and displays the persisted
  list shared by authorized users of that deployment.
- The page provides one location-entry form with an accessible choice between city/state
  and latitude/longitude input modes; it shows only the controls for the selected mode.
- After validating the active form, Add sends the location to `POST /weather/locations`.
  The new item appears in the shared list only after the server confirms it was saved.
- Each saved item is a readable, display-only item with its own Remove action. Add and
  remove changes are visible to other users on their next list refresh.
- Remove calls `DELETE /weather/locations/{location_id}`. On success, the item is removed
  from the list, unreferenced weather documents and their chunks/vectors are deleted,
  and documents still used by another saved location are retained. The response can
  report deleted-document/chunk and retained-shared-document counts.
- If adding or removing fails, the page displays an error and does not present the
  unsuccessful change as saved or complete. A failed removal leaves the item available
  for retry.
- Editing an existing item requires removing and adding it again.
- The page validates required input before adding it and displays validation errors next
  to the relevant controls. An empty selected-location list cannot be synchronized.
- The Sync action submits only the saved location IDs to `POST /weather/sync`.
- While a request is pending, the page indicates progress and prevents duplicate
  submissions of the same action.
- After completion, the page displays the result for each location, including status,
  documents synchronized, and any error or request-budget exhaustion. It communicates
  partial success distinctly from complete failure.
- The interface informs users that each location is limited to 20 outbound NWS request
  attempts per sync, including retries.

## Semantic search

- Users can enter a non-empty natural-language query and submit it to
  `POST /weather/search`; the interface uses the endpoint's default result count.
- While a search is pending, the page indicates progress and prevents duplicate
  submissions of that action.
- Results are shown in returned relevance order. Each result displays all associated
  saved locations, source type, headline when available, matched chunk text, similarity,
  synchronization time, and applicable issue/effective or forecast-period timestamps.
- The displayed freshness fields map to `source_type`, `synced_at`, `issued_at`,
  `effective_at`, `period_start`, and `period_end` from the search response.
- Embedding remains a separate documented batch-process step; the page does not launch
  that process. If there are no matches, the page explains that documents may need to be
  embedded before they are searchable.
- Null or inapplicable timestamps are labeled as unavailable rather than omitted or
  presented as current.
- An empty result set and request/validation errors receive clear user-facing messages.

## Accessibility and safety

- Every input and action has a programmatic label; status and error messages are not
  communicated by color alone.
- The interface does not accept or render user-provided HTML. Search results are treated
  as text.
- The interface does not collect or persist credentials; local access remains loopback
  only, and deployed access is controlled by Databricks workspace permissions.
