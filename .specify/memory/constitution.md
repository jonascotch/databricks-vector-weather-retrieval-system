<!--
Sync Impact Report
Version change: 1.1.1 → 1.2.0
Modified principles:
- III. Retrieval Integrity: require source identity/type and freshness timestamps in each result.
- V. Testability and Observability: explicitly test retrieval provenance and freshness metadata.
Added or expanded sections:
- Data and Platform Constraints: permit isolated local PostgreSQL/pgvector for development and tests.
- Delivery and Quality Gates: require tests for retrieval source and freshness metadata.
Removed sections: None
Follow-up TODOs: None
-->
# Databricks Weather Retrieval System Constitution

## Core Principles

### I. Source Fidelity and Provenance
The ingestion pipeline MUST preserve the weather provider's meaning and associate each
stored document with its source, location or query scope, observation or forecast time,
and retrieval time when available. It MUST distinguish observations from forecasts and
retain enough provenance to evaluate freshness and trace results to their source.
This prevents retrieval from presenting stale or forecast information as current fact.

### II. Reproducible, Idempotent Ingestion
Harvesting MUST normalize free-text API responses into a documented canonical document
schema before persistence. Chunking and embedding MUST be deterministic for fixed input
and configuration, and reprocessing the same source records MUST NOT create unintended
duplicates. Changes to normalization, chunking, or embedding configuration MUST be
identifiable and MUST NOT silently mix incompatible vectors. This makes data refreshes
repeatable and vector results interpretable.

### III. Retrieval Integrity
Each stored vector MUST remain associated with the exact text chunk and its source
document. Document and embedding records MUST follow the project's established
separation pattern for raw documents and embeddings, including the
`ticker_news_documents` / `ticker_news_embeddings` pattern where applicable. Query and
document vectors MUST use compatible models and dimensions, matching the project's
existing embedding dimensionality conventions. Semantic search MUST use pgvector's
`<=>` cosine-distance operator, order results by ascending distance, apply explicit
result limits, and return source and freshness metadata with every result. Each result
MUST include the source document identifier, source type, location, applicable provider
issue/effective timestamps or forecast-period timestamps, and the synchronization time.
Unavailable or inapplicable timestamp fields MUST be represented explicitly as null.
Retrieval MUST NOT claim that similarity alone establishes truth or current conditions.

### IV. Contract-Driven API
The Flask REST API MUST expose retrieval through a documented, stable request and
response contract. Its semantic-search endpoint MUST execute the cosine-distance query
against pgvector using `<=>`. A Python batch job MUST write documents, chunks, and vectors
to Lakebase through `psycopg2`, with bounded batches and transactional writes. The API
and batch job MUST validate inputs, return or report failures explicitly, and MUST NOT
leak credentials or internal connection details. The API retrieves evidence; any later
answer-generation layer MUST preserve the distinction between retrieved material and
generated conclusions.

### V. Testability and Observability
Ingestion transformations and retrieval behavior MUST be testable independently of live
external services. Tests MUST cover normalized document shape, chunk/vector association,
embedding dimension compatibility, batch-job idempotence, cosine-distance ordering,
retrieval source and freshness metadata, API validation and error behavior, and database
retrieval integration at the relevant boundaries. Operational logs MUST provide
actionable failure context without exposing secrets. This keeps failures diagnosable and
protects data integrity across the pipeline.

## Data and Platform Constraints

The deployed application MUST use Lakebase (Postgres) with `pgvector` as its persisted
semantic-search datastore, `sentence-transformers` for text embeddings, and Python
`psycopg2` for batch writes unless an approved amendment changes the architecture.
Isolated local development and automated tests MAY use local PostgreSQL with `pgvector`
without Databricks credentials. Local databases MUST use the same schema, embedding
dimensions, and vector operator semantics as Lakebase; local data MUST remain
development/test data and MUST NOT replace or be promoted as the deployed Lakebase
datastore. Weather text MUST be harvested from a real-world public API in accordance
with that provider's published access terms and rate limits. The schema MUST preserve
normalized raw documents separately from their chunk embeddings, using relational
references that prevent orphaned vectors. Vector columns MUST use the project's
established embedding dimensions, and the embedding model and relevant configuration
MUST be recorded to support compatible retrieval. Credentials and connection details
MUST be supplied through the established secret or environment configuration, never
committed to source control. Persisted text and vectors MUST retain the metadata needed
for source traceability and freshness evaluation.

## Delivery and Quality Gates

Changes MUST include focused automated tests for introduced or changed behavior. Tests
that require Lakebase, `pgvector`, model downloads, or the weather provider MUST be
separable from the default test suite and use controlled dependencies or explicit
configuration. Before a change is accepted, reviewers MUST verify the relevant tests,
API contract, data lineage, secret handling, retrieval freshness metadata, and compliance
with this constitution. Any exception MUST be documented with its rationale and scope.

## Governance

This constitution governs project design and delivery decisions. Amendments MUST be
proposed as explicit edits, reviewed for impact on existing data, APIs, and operational
behavior, and approved by the project maintainers before adoption. Every amendment MUST
update the version and last-amended date and include a concise rationale in its change
description.

Versioning follows semantic versioning: MAJOR for backward-incompatible changes to
principles or governance, MINOR for new principles or materially expanded requirements,
and PATCH for clarifications and non-semantic wording changes. During implementation and
review, contributors MUST check relevant work against these principles and quality gates.
Reviewers MUST call out non-compliance; accepted exceptions MUST identify the affected
requirement, justification, and any compensating measure. The constitution MUST be
reviewed whenever the data source, storage or embedding architecture, or retrieval
contract materially changes.

**Version**: 1.2.0 | **Ratified**: 2026-11-22 | **Last Amended**: 2026-11-22
