# Frontend Contract: Weather Vector Search

## Entry point

The Flask application serves the single-page interface at `/`. The page uses same-origin
requests to the weather REST endpoints and requires no separate frontend service,
Databricks credentials, or build step in local development.

## Location synchronization

- Users can add and remove one or more U.S. locations.
- A location is either a city/state name or a latitude/longitude pair.
- The page validates required input before sending a request and displays validation
  errors next to the relevant controls.
- The Sync action submits all selected locations to `POST /weather/sync`.
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
- Results are shown in returned relevance order. Each result displays the location,
  source type, headline when available, matched chunk text, similarity, synchronization
  time, and applicable issue/effective or forecast-period timestamps.
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
