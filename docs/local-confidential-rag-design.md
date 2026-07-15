# Local Confidential RAG Design

## Status

This is the approved design boundary for a future local RAG capability. It is
not enabled in the current application. `confidential` documents must continue
to be excluded from the Knowledge Base until every component below is deployed
and verified.

## Security invariant

For confidential RAG, document text, embeddings, questions, retrieved chunks,
and generated answers must remain on infrastructure controlled by the
deployment. There must be no fallback to OpenAI, no shared vector namespace
with standard documents, and no external telemetry containing document text.

## Target flow

```text
confidential document
  -> local text extraction
  -> local chunking
  -> local embedding service
  -> confidential vector collection
  -> local LLM service
  -> sourced answer
```

Standard documents keep the existing OpenAI embedding and answer flow. The two
flows are selected only by `documents.processing_mode`; a client-supplied flag
must never select the provider.

## Provider contracts

Implement two explicit adapters before enabling the feature:

1. `LocalEmbeddingProvider.embed(texts) -> vectors`
   - private base URL, fixed model and fixed vector dimension;
   - batch size and request timeout configured separately from OpenAI;
   - reject a result with the wrong vector count or dimension;
   - do not record an `OpenAIUsageLog` for local work.

2. `LocalAnswerProvider.answer(question, context, history) -> GroundedAnswer`
   - private base URL and local model only;
   - use the same grounded-answer schema and source validation as standard RAG;
   - disable provider-side request logging or redact all document-derived data.

The first supported deployment may use OpenAI-compatible local endpoints (for
example, Ollama/vLLM behind the private Docker network), but the adapter must
not import or instantiate the OpenAI cloud client.

## Data isolation and schema changes

`document_chunks.embedding` has one fixed pgvector dimension today, so local
and cloud embeddings must not share it unless their dimension and model are
identical. Before rollout, introduce either:

- a `document_chunk_embeddings` table with `provider`, `model`, `dimension`,
  and separate vector columns/collections; or
- separate PostgreSQL/pgvector databases for standard and confidential data.

Every retrieval query must filter by owner, active document state, processing
mode, and provider. Cross-mode retrieval is forbidden. The confidential index
must never be queried by standard conversations, and vice versa.

## Required configuration

Keep the feature disabled by default. When implementation starts, introduce
separate values such as:

```dotenv
LOCAL_CONFIDENTIAL_RAG_ENABLED=false
LOCAL_EMBEDDING_BASE_URL=http://local-embeddings:8080
LOCAL_EMBEDDING_MODEL=...
LOCAL_EMBEDDING_DIMENSIONS=...
LOCAL_LLM_BASE_URL=http://local-llm:8081
LOCAL_LLM_MODEL=...
LOCAL_RAG_REQUEST_TIMEOUT_SECONDS=60
```

Production validation must reject an enabled local confidential RAG mode when
any local endpoint/model/dimension is missing. It must also reject public URLs
unless the deployment explicitly allows them.

## Rollout gates

Do not enable confidential RAG until all of the following pass:

1. Network tests prove no OpenAI client or DNS request is made for a
   confidential document or question.
2. Integration tests prove owner and processing-mode isolation for indexing and
   retrieval.
3. Dimension/model mismatch tests fail before vectors are persisted.
4. Local provider outage marks only the confidential indexing job as retryable
   or failed; it must not fall back to cloud processing.
5. Browser tests label confidential chat as local-only and keep standard and
   confidential conversations separate.

## Current behavior

Until this design is implemented, confidential documents remain locally
processed but are not indexed and cannot be used in document chat. This is an
intentional privacy guarantee, not a degraded fallback.
