# Local Mem0 OSS

OSS is the primary memory backend. This directory builds the native Mem0 server from commit `c7ee362aff94a369af70f13f2b4f853f6793ff4c` with core `mem0ai==2.0.19`, separate PGVector storage and native history, plus a small keyset-pagination extension. It is not a Platform-compatible gateway, Service database mirror, or fake persistence service.

## Start

```bash
make setup       # Service stores, Langfuse, Mem0 and Service migrations
make mem0-up     # Only the checkout-owned Mem0 stack
make mem0-logs
make mem0-down   # Stop containers; preserve volumes
```

`dev/service/local.toml` selects OSS at `http://127.0.0.1:18888` with the public fixture key `local-mem0-api-key`. The helper derives the Compose project, API key and host port from the selected `SERVICE_CONFIG`, not ambient `MEM0_LOCAL_*` variables or `.env`. Only a local `127.0.0.1` OSS endpoint is managed; external OSS and Platform are operator-owned. PostgreSQL and the embedding fixture have no host ports. The Mem0 process is non-root.

The default embedding endpoint is a deterministic, hashed-word, 128-dimensional fixture. It validates storage, HTTP contracts, CRUD, scope isolation, and pagination without paid credentials. **It does not validate semantic embedding quality or real LLM inference.** The separate Service scripted model is not a memory model. Explicit writes use `infer=false`, so neither writes nor retrieval require chat completion. The fixture rejects chat requests instead of pretending to perform extraction.

Service running does not enable Agent memory. Set the selected Agent revision's `config.memory` explicitly as shown in the [Service guide](../../docs/a13n-service/memory.md). This work adds no Console page or UI switch.

## Use real models

Export private settings in your shell or source a Git-ignored private file before `make mem0-up`. Never paste or commit keys. The following are placeholders; provide values from your deployment:

```bash
export MEM0_OSS_EMBEDDING_BASE_URL='https://embedding.example/v1'
export MEM0_OSS_EMBEDDING_MODEL='your-embedding-model'
export MEM0_OSS_EMBEDDING_API_KEY="$PRIVATE_EMBEDDING_KEY"
export MEM0_OSS_EMBEDDING_DIMENSIONS='1536' # Actual output size, not a guess
export MEM0_OSS_COLLECTION='memories_real_model'
make mem0-up
```

Use `http://host.docker.internal:PORT/v1` for an endpoint running on the host; container loopback is not the host. Endpoint protocol must be OpenAI-compatible embeddings. `MEM0_OSS_EMBEDDING_SEND_DIMENSIONS=true` additionally sends the optional `dimensions` request parameter; leave it false for endpoints that reject that parameter. The configured database size must always match actual embeddings.

LLM configuration is independent, with a separate key:

```bash
export MEM0_OSS_LLM_BASE_URL='https://llm.example/v1'
export MEM0_OSS_LLM_MODEL='your-chat-model'
export MEM0_OSS_LLM_API_KEY="$PRIVATE_LLM_KEY"
```

These settings configure the native server for operator-owned inference workflows. Service memory writes remain explicit and inference-disabled; configuring an LLM does not enable terminal transcript extraction.

Changing embedding model or dimensions does not re-embed existing records. Startup checks the existing collection's vector size and refuses a mismatch without deleting data. Restore the old configuration or choose a new collection and migrate explicitly. Even equal-dimensional models can use incompatible vector spaces: choose a new collection when changing the model. `make dev-reset` resets Service-owned storage, not Mem0 volumes. There is no memory reset command.

The three keys have distinct owners: `memory.api_key` authenticates Service to Mem0; `MEM0_OSS_EMBEDDING_API_KEY` and `MEM0_OSS_LLM_API_KEY` authenticate Mem0 to its model endpoints. Check `make mem0-logs` for native connectivity or dimension errors. Local startup verifies authentication and pagination, not semantic quality. Development diagnostics stay in startup logs and this guide, never product response schemas.

## API walkthrough

Use an authenticated human User bearer token with Workspace access. Obtain immutable Workspace/Agent/Thread IDs through the Native API. Do not substitute provider namespace hashes or model-visible aliases.

```bash
export SERVICE_URL='http://127.0.0.1:8000'
# Set WORKSPACE_ID and SERVICE_TOKEN privately.
BASE="$SERVICE_URL/api/v1/workspaces/$WORKSPACE_ID/memories"
curl --fail-with-body -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Prefer concise implementation explanations."}' \
  "$BASE?scope=user"
curl --fail-with-body -H "Authorization: Bearer $SERVICE_TOKEN" \
  "$BASE?scope=user&limit=50"
curl --fail-with-body -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H 'Content-Type: application/json' -d '{"query":"implementation explanations"}' \
  "$BASE/search?scope=user"
# Use the returned native memory ID for GET, PUT or DELETE /memories/{id}.
```

For an Agent use `scope=agent&subject_id=$AGENT_ID`; for a Thread use `scope=thread&subject_id=$THREAD_ID`. User scope forbids `subject_id`. Viewer is read-only; Runner can write. Add/update preserve nonblank text exactly. Do not automatically repeat `memory_write_unconfirmed`: a timed-out write may have committed.

## Pagination extension

The upstream pinned server's unpaginated list is not sufficient for management APIs. `patches/pgvector-pagination.patch` adds `PGVector.list_page`, using the native filter builder, UUID ascending keysets, and `LIMIT top_k + 1`. `pagination.py` exposes authenticated `GET /memories/page` before the dynamic ID route. Other operations retain the upstream API, including `POST /search`; Platform DTOs are not involved.

The patch is deliberately small and upstreamable. Build applies it with zero fuzz and guarded source replacements so source drift fails visibly. An upstream upgrade must rerun native integration tests rather than silently keep a compatibility fallback. Expiry filtering can yield empty pages with continuation; stop only at null `next_cursor`. Pagination is not a snapshot under concurrent inserts.

## Validation

Unit/contract tests need no remote credentials:

```bash
uv run --locked pytest packages/a13n-harness/tests/test_mem0.py packages/a13n-harness/tests/test_mem0_backends.py
uv run --locked pytest packages/a13n-service/tests/memory
uv run --locked pytest dev/service/tests
```

For real native OSS tests, start a disposable instance or use the local stack, then explicitly opt in. Tests create records and delete only their own IDs; the pagination test writes more than 1,000 records. Do not point this at production.

```bash
TEST_MEM0_OSS_URL=http://127.0.0.1:18888 \
TEST_MEM0_OSS_API_KEY=local-mem0-api-key \
uv run --locked pytest packages/a13n-service/tests/memory/test_oss_integration.py -q
```

This exercises real Service HTTP authorization, native CRUD/search, PGVector pagination and expired pages. Platform validation uses the installed native SDK with mocked HTTP transport only; no live Platform key is required. Run Harness and Service test paths separately because their test packages use the same import namespace.
