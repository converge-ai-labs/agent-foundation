# Local Mem0 OSS

OSS is the primary memory backend. This directory builds **unmodified** native Mem0 server sources from commit `c7ee362aff94a369af70f13f2b4f853f6793ff4c` with core `mem0ai==2.0.19`, separate PGVector storage and native history. It adds no source patch, private route, Platform gateway, or Service memory mirror. Existing OSS deployments providing the public API can be connected directly; this development image is not an integration requirement.

## Start

```bash
make setup       # Service stores, Langfuse, Mem0 and Service migrations
make mem0-up     # Only the checkout-owned Mem0 stack
make mem0-logs
make mem0-down   # Stop containers; preserve volumes
```

`dev/service/local.toml` selects OSS at `http://127.0.0.1:18888` with the public fixture key `local-mem0-api-key`. The helper derives the Compose project, API key and host port from the selected `SERVICE_CONFIG`, not ambient `MEM0_LOCAL_*` variables or `.env`. Only a local `127.0.0.1` OSS endpoint is managed; external OSS and Platform are operator-owned. PostgreSQL and the embedding fixture have no host ports. The Mem0 process is non-root.

The default embedding endpoint is a deterministic, hashed-word, 128-dimensional fixture. It validates storage, HTTP contracts, CRUD, scope isolation, and bounded listing without paid credentials. **It does not validate semantic embedding quality or real LLM inference.** The separate Service scripted model is not a memory model. Explicit writes use `infer=false`, so neither writes nor retrieval require chat completion. The fixture rejects chat requests instead of pretending to perform extraction.

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

Changing embedding model or dimensions does not re-embed existing records. Configuration goes through native `POST /configure`; the helper does not inspect or modify the database. A mismatched existing collection can fail on native reads or writes. Restore the old configuration or choose a new `MEM0_OSS_COLLECTION` and migrate explicitly. Startup is not a guarantee that existing vectors match the selected model. Even equal-dimensional models can use incompatible vector spaces: choose a new collection when changing the model. `make dev-reset` resets Service-owned storage, not Mem0 volumes. There is no memory reset command.

The three keys have distinct owners: `memory.api_key` authenticates Service to Mem0; `MEM0_OSS_EMBEDDING_API_KEY` and `MEM0_OSS_LLM_API_KEY` authenticate Mem0 to its model endpoints. Check `make mem0-logs` for native connectivity or dimension errors. Local startup applies model settings through native `POST /configure` and verifies authenticated `GET /memories`, not semantic quality. Development diagnostics stay in startup logs and this guide, never product response schemas.

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

## Native listing and client-side pages

The adapter calls public `GET /memories` with a trusted subject and `top_k`. Service loads at most 1,000 records by default and returns `pagination=null`: a bounded set, not a complete collection or a native page cursor. Records beyond that subset remain stored and searchable. A short or empty result does not prove completeness because native storage and expiry filters can limit results.

Clients may slice these loaded records into local display pages. Show a hint such as "Showing up to 1,000 loaded memories. More may exist; search for relevant memories." Label counts as loaded records rather than total memories. Do not offer a next server page, a guaranteed total, or a full-export claim. Platform preserves its native pagination separately; it does not define the OSS contract. See [Service memory](../../docs/a13n-service/memory.md) for the response shape. No frontend code is added here.

The upstream Dashboard follows this same bounded approach: [PR #5753](https://github.com/mem0ai/mem0/pull/5753) adds `top_k=1000` loading with client-side pages. [Issue #3751](https://github.com/mem0ai/mem0/issues/3751) discusses missing OSS traversal. Neither is a reason to patch the user's deployment.

## Standalone Compose

The dev helper performs both startup and native configuration. When running Compose directly for disposable integration tests, perform the same two steps:

```bash
MEM0_LOCAL_PORT=18889 docker compose --env-file /dev/null \
  --project-name a13n-mem0-test --file dev/mem0/compose.yaml \
  up -d --build --wait
uv run --locked python -m dev.mem0.configuration --base-url http://127.0.0.1:18889
```

Run this configuration command only against your checkout-owned test instance: it changes the explicitly selected endpoint. It uses `MEM0_LOCAL_API_KEY` (default public fixture key) and `MEM0_OSS_*` model settings from the invoking shell, and persists configuration through Mem0's native API. The Service adapter never configures the backend; the dev helper skips external operator-owned backends. The Compose health check only checks the OpenAPI endpoint; configure before writing memories. No patched initialization path exists.

## Validation

Unit/contract tests need no remote credentials:

```bash
uv run --locked pytest packages/a13n-harness/tests/test_mem0.py packages/a13n-harness/tests/test_mem0_backends.py
uv run --locked pytest packages/a13n-service/tests/memory
uv run --locked pytest dev/service/tests
```

For real native OSS tests, start a disposable instance or use the local stack, then explicitly opt in. Tests create records and delete only their own IDs; the bounded-list test writes more than 1,000 records and confirms only 1,000 are loaded without claiming completion. Do not point this at production.

```bash
TEST_MEM0_OSS_URL=http://127.0.0.1:18888 \
TEST_MEM0_OSS_API_KEY=local-mem0-api-key \
uv run --locked pytest packages/a13n-service/tests/memory/test_oss_integration.py -q
```

This exercises real Service HTTP authorization, native CRUD/search, the 1,000-record list bound and native expiry filtering. Platform validation uses the installed native SDK with mocked HTTP transport only; no live Platform key is required. Run Harness and Service test paths separately because their test packages use the same import namespace.
