# Local Service development

This directory owns the local Service environment: its explicit TOML configuration, PostgreSQL and Redis containers, local Langfuse, state reset tooling, and fictional sample content. It does not depend on Console. Console, SDKs, the service CLI and direct API clients can all use the resulting Service.

## Memory backend

The default configuration also starts checkout-owned [Mem0 OSS](../mem0/README.md) on port 18888. It uses native PGVector persistence with deterministic local embeddings, not real semantic inference. Agent memory remains opt-in through `config.memory`; no frontend change is required. Use `make mem0-up`, `make mem0-down`, and `make mem0-logs` independently. Service reset and shutdown preserve Mem0 volumes; real model setup and dimension changes are documented in the Mem0 guide.

## Daily workflow

These local tools support macOS and Linux. From the repository root, with Docker running:

```sh
make dev
```

This installs locked dependencies, starts PostgreSQL, Redis and the complete local Langfuse stack, verifies Langfuse project authentication, applies committed migrations, and runs Service, the scripted model and Console. No `.env`, manual Langfuse project creation, API-key copying or OTEL exports are needed.

`make setup`, also used by `make dev` and `make service-dev`, checks Docker before starting the containers. If the selected daemon is already ready, it proceeds immediately. On macOS, an unavailable local Docker Desktop daemon triggers an attempt to open Docker Desktop and wait up to 120 seconds for readiness. Docker must already be installed. On Linux or with another Docker endpoint, start the selected daemon yourself; the tools preserve your Docker context and `DOCKER_HOST`.

Use `make setup` to prepare Python dependencies, infrastructure and schema without starting an application listener. It preserves existing data and credentials. Use `make service-dev` for Service and the scripted model without Console or its frontend dependencies. Default addresses are:

| Component      | Address                     |
| -------------- | --------------------------- |
| Console        | `http://127.0.0.1:5173`     |
| Service        | `http://127.0.0.1:8000`     |
| Scripted model | `http://127.0.0.1:18080/v1` |
| Langfuse       | `http://127.0.0.1:3000`     |
| Langfuse media | `http://127.0.0.1:3001`     |

Ctrl+C stops the applications, not the Docker infrastructure. The outer launcher stops both Service and Console if either exits. Console's separate Vite command remains documented in its README.

A new installation follows the normal initial administrator invitation flow; the single-use link is printed in protected Service startup logs when SMTP is not configured. For fictional accounts and example conversations, explicitly run `make dev-reset STATE=seeded` **before** starting applications. This deletes existing Service data; neither setup nor daily startup implicitly resets or seeds it.

After a seeded reset, sign in to Console as `admin@example.com` with the public test password `local-public-password-123`. `builder@example.com`, `runner@example.com`, and `viewer@example.com` use the same public test password with different Workspace roles. All identities and content are fictional. None of these credentials grants access to any real deployment.

```sh
make dev-down                 # Stop infrastructure; preserve data
make dev-reset STATE=empty    # Rebuild the installation without business data
make dev-reset STATE=seeded   # Rebuild with accounts, resources and history
```

Stop Service before resetting. The launcher holds a local lifecycle lock; reset also refuses active PostgreSQL or Redis clients started outside that launcher. Reset never kills unrelated application processes. Dependencies may start during reset, but no Service listener or Console is left running when reset completes. The seeded reset temporarily opens the ordinary Service application runtime to provision through its real identity, resource and execution paths, then closes it.

`empty` contains the migrated schema. The next Service startup issues the normal initial administrator invitation; without SMTP, its single-use link is printed in protected startup logs. `seeded` provisions more than 60 Agents, Skills and Assets, 120 bulk Sessions plus distinct execution journeys, and an additional empty Workspace. It checks the resulting data before reporting success and writes exact counts and a scenario-to-resource index to `var/service/seed-report.md` and `seed.json`.

The local profile sets `[worker].concurrency = 8`. Bulk Session creation uses that same limit (or the number of Sessions, if smaller). Each execution slot has its own Environment and directory under `var/service/files/bulk/slot-NN`; only successive Runs in the same slot reuse a directory. The slot assignments are recorded in `seed.json`. Long conversations and dependent retry/feedback journeys remain sequential. Override the limit with `A13N_SERVICE_WORKER_CONCURRENCY` when running on a smaller machine; it applies to both seeding and the local Service. The local database pool keeps up to 60 connections with 20 overflow connections; Redis permits up to 256 connections, including the blocking control-signal reads for active Runs. One observer polls the Workspace Run collection for all bulk slots, sizing each page to the slot count and stopping after finding all pending Run identities. It waits 100 ms between scans. A slot starts its next Run only after the previous Run is sealed with the expected outcome. Verification uses the same Run collection and reads independent Thread collections and complete transcripts with at most eight concurrent requests, retaining pagination and relationship checks.

## Local traces

`[service].deployment_environment_name = "local"` in `local.toml` is the single deployment label. Service exports it as `deployment.environment.name`; the dev launcher also uses that value for `OTEL_RESOURCE_ATTRIBUTES`. New Langfuse observations show `Env: local`. This is not an execution Environment or provider selection. Restart the local process after changing the label; previous observations retain their original environment.

`local.toml` enables tracing with standard input/output content and selects the local Langfuse query project. The development launcher uses that same project to initialize Langfuse and configure Service's standard OTLP/HTTP exporter. Content is retained in local Docker volumes; do not use real private inputs unless you intend to store them there. Langfuse telemetry is disabled in the Compose file.

The public local Langfuse login is `dev@agent-foundation.local` / `agent-foundation-local`. The organization and project are `Agent Foundation Local`. The public test API keys are in `[observability.query]` in `local.toml`. Changing initialization keys does not rotate keys in existing Langfuse data; restore the matching configuration or explicitly reset the trace store.

```sh
make langfuse-up       # Start and authenticate only the trace backend
make langfuse-test     # Write a real OTLP trace and query it through the Service adapter
make langfuse-down     # Stop Langfuse, retaining traces
make langfuse-reset    # Delete only Langfuse data, never Service data
```

`make setup` checks web/worker readiness and project authentication; the explicit smoke test additionally waits for ingestion and verifies input search and trace detail. Application runtime readiness still does not depend on exporter availability. Service state resets never delete Langfuse volumes. Seeded resets export their fictional execution journeys to the same trace store.

The default Service composition supplies a Run/IAM-backed `TraceAccessAuthorizer`. With the query backend configured, Console and the Service Trace Query API return traces only for retained Runs the caller can read with `trace.read` permission. Backend correlation is checked against Service records after each backend read. The smoke test uses fixture-owned Run/IAM records and the default authorizer to verify OTLP transport, backend reads, and authorized HTTP list/detail responses.

### Using Logfire

The commented alternative in `local.toml` supports both OTLP export and Console Trace Query. Copy the template to `local.private.toml` if you do not already have a private file; otherwise update its existing `[observability.query]` table. Select `provider = "logfire"`, set `logfire_base_url` to the project's US or EU regional root URL, and choose a timezone-aware `logfire_history_from` lower bound for queryable history. This lower bound does not change Logfire retention or delete data.

Logfire export needs a project write token in `LOGFIRE_TOKEN`. Query needs a project read token in `logfire_read_token` or `A13N_SERVICE_OBSERVABILITY_QUERY_LOGFIRE_READ_TOKEN`. Use the same project for both, but do not assume a write token also authorizes queries. Keep real credentials in private files or injected environment variables, never in the public template or Console configuration. The launcher does not automatically load `.env` files; explicitly source a private shell environment file or export the variables before invoking Make.

```sh
export LOGFIRE_TOKEN='YOUR_LOGFIRE_WRITE_TOKEN'
export A13N_SERVICE_OBSERVABILITY_QUERY_LOGFIRE_READ_TOKEN='YOUR_LOGFIRE_READ_TOKEN'
make dev SERVICE_CONFIG=dev/service/local.private.toml
```

The exporter defaults to the selected query provider and, for Logfire, its configured regional base URL. `A13N_DEV_TRACE_BACKEND` and `LOGFIRE_BASE_URL` remain explicit export-only overrides. For Logfire export without Console queries, select `provider = "none"`, export `A13N_DEV_TRACE_BACKEND=logfire`, and set `LOGFIRE_BASE_URL` for a non-US project; no read token is needed in that mode. No Logfire SDK is required. With `trace_content = "standard"`, input/output content is uploaded to Logfire; use `"none"` to omit it.

With Logfire selected, setup still prepares the owned PostgreSQL and Redis stores but does not start or authenticate local Langfuse. Switching providers neither resets Service data nor migrates old traces. Existing Langfuse containers and volumes are retained; `make langfuse-down SERVICE_CONFIG=dev/service/local.private.toml` can stop that checkout's old stack without deleting it. Service reset never clears the remote Logfire project.

### Incremental Logfire execution check

With an existing seeded baseline and Service already running with the Logfire configuration above, explicitly enable the live regression from another terminal:

```sh
A13N_TEST_LOGFIRE_CONFIG=dev/service/local.private.toml \
A13N_TEST_LOGFIRE_OUTPUT=var/logfire-check-$(date +%Y%m%d-%H%M%S) \
uv run pytest dev/service/tests/test_logfire_integration.py -q --tb=short
```

This test does not start infrastructure, reset stores, initialize identity, or reseed resources. It signs in through the ordinary local password flow, verifies the retained Agents use the loopback scripted model and MCP fixture, and creates eight additional fictional Runs: plain output, tool success, tool failure, long output, provider failure, retry, client-tool waiting, and feedback completion. Runs and their Attempt IDs remain in Service; their standard content is uploaded to the configured Logfire project. Never point this test at real accounts or a paid model.

The check waits boundedly for ingestion, compares raw completed Logfire records with the existing provider adapter and production Run/IAM-authorized HTTP API, and validates Attempt correlation, full/compact projections, native messages/events, reported usage, absent costs, parent relationships, pagination, history bounds, and anonymous/cross-Workspace denial. It records Run/Attempt IDs, raw records, public projections, and a final `verified.json` in the output directory. Use a new directory on each invocation; earlier evidence is not overwritten. Without `A13N_TEST_LOGFIRE_CONFIG`, ordinary test runs skip all live execution and remote reads.

Logfire read quotas are independent of OTLP ingestion. The test spaces backend reads by ten seconds by default, allowing extra time for observation pages that require both root authorization and child reads. Expect several minutes of validation. `A13N_TEST_LOGFIRE_QUERY_INTERVAL` changes that interval for the selected project's budget; avoid concurrent Console trace browsing during this check. Rate limiting remains a safe unavailable response, not evidence that a Run failed or its telemetry is absent. The test reports transient HTTP 429 backend reads and HTTP 503 Service trace reads, waits 60 seconds, and retries each at most twice; persistent failures still fail the test. It never retries Run creation, retry, feedback, or other mutations.

After an interrupted or unsuccessful query check, set `A13N_TEST_LOGFIRE_RUNS` to the earlier output directory's complete `runs.json` to repeat readback without creating more Runs. Keep the same Service configuration and seed baseline and choose a new output directory. This explicitly reuses execution evidence; it does not claim to have executed new Runs.

### Upgrading an older local Langfuse stack

Older `make langfuse-up` versions used the shared Compose project `agent-foundation-langfuse-dev`. The launcher warns if it is still running; it never stops it automatically. To release its ports without deleting its data:

```sh
docker compose --env-file /dev/null \
  --project-name agent-foundation-langfuse-dev \
  --file dev/observability/langfuse.compose.yaml stop
make setup
```

The new checkout-owned stack has separate volumes. Old traces are not migrated or deleted; retain the old project and volumes if you need to inspect that history.

### Export environment isolation

The local launcher replaces inherited `OTEL_*` variables with the selected local profile and announces this when applicable. This prevents an ambient remote collector or trace-specific endpoint/header override from receiving local content. Loopback HTTP traffic also bypasses inherited proxies; other destinations retain their proxy policy. It does not change the calling shell. The production CLI continues to accept standard OTEL environment settings independently from query configuration. To opt out of local Langfuse, set `observability.tracing = false` and `observability.query.provider = "none"` in the selected TOML.

## Seed coverage

| Area                  | Retained scenarios                                                                                                                                                                                                                                                    |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity              | Administrator, Builder, Runner and Viewer accounts; effective permission checks; custom names and images alongside default avatars; pending/resent and revoked invitations                                                                                            |
| Credentials           | Active, expiring, naturally expired and revoked personal API keys; Builder/Runner/Viewer service accounts, including a disabled account; normal audit history                                                                                                         |
| Agents                | Default-page pagination, short/long/Unicode names, empty/long descriptions, no Skills versus pinned Skills, multiple revisions, restore, independent duplicate, disabled and archived Agents with real historical Runs                                                |
| Skills                | More than one page of uploaded packages, nested reference files, multiple revisions, referenced and unused Skills, deletion through normal reference checks                                                                                                           |
| Assets                | Markdown, CSV, JSON, PNG, WAV, ZIP, HTML, code, unknown binary, empty and large text files; long Unicode filenames; multiple Run input attachments; a Run-published artifact with immutable bytes and provenance                                                      |
| Conversations         | Empty Thread, success, provider failure and retry, long continued conversation, fork, waiting client tool, completed feedback, cancellation, retry after cancellation, queued follow-up, structured output, malformed JSON failure, and hosted parent/child execution |
| Tools and connections | Local MCP discovery, successful tool call and tool failure, ready/pending/disabled MCP connections, pending/disabled Connector connections, active/disabled Connector Providers, fictional Application Accounts and Targets with reception off                        |
| Environments          | Shared local files, read-only and read-write recipes, unused instances, revised and archived templates, disabled Provider, and real preparation failure against a missing owned directory                                                                             |
| Integrity             | Unique pagination results, expected outcomes, sealed Runs, successful/waiting Run transcripts, preserved revision and retry/feedback lineage, empty-Workspace isolation, and downloaded byte hashes after restart                                                     |

The fixture model and protocol endpoints run only on loopback. No real model provider, connector account, Slack workspace or OAuth authorization is contacted. Application Accounts use deliberately fictional credentials and keep reception off. Connector Providers use the reserved `https://connector.invalid` placeholder; no discovery or OAuth request is made for their pending connections.

Records get normal creation times and fresh identifiers on every reset. Expired API-key coverage waits for a short-lived key to expire naturally. The baseline is reproducible in content and relationships, not a byte-identical database image. Bulk Sessions execute concurrently in isolated local workspaces. Runs execute in sequence when sharing a local filesystem Environment. Waiting client-tool requests and queued input remain available for interaction after startup; no Run is left accepted or running when reset completes.

The local model returns scripted Markdown in English and Chinese. Send `[long]` for a long answer, `[slow] [long]` to inspect streaming and scrolling, or `[fail]` to exercise an intentional model error. The dedicated fixture Agents also support `[client]`, `[mcp]`, `[mcp-fail]`, `[structured]`, `[structured-invalid]`, and `[delegate]` for their corresponding scenarios. These directives select fixture behavior; they are not real model reasoning. Run `python -m dev.service model` if you start Service through its production CLI and want the fixture process separately.

Transient loading/streaming states must be exercised with a running Service. Real provider compatibility, external OAuth consent, production observability and multi-day retention behavior remain separate integration tests. A failed Run that never produced retained Items is recorded in the coverage report; successful and waiting Runs are required to expose their retained transcript.

## Configuration and ownership

`local.toml` is a committed **local-only** configuration. All supplied credentials are public test values; never put a real secret in it. Real credentials belong in explicit environment overrides or the normal resource credential flow, never in tracked sample files.

`SERVICE_CONFIG` selects the same explicit TOML for development, database and Langfuse Make targets. There is no implicit overlay or `.env` loading. For a custom configuration, pass `make dev SERVICE_CONFIG=dev/service/my-local.toml`; keep private copies out of Git. Console's port and upstream follow the selected IAM origin and Service listener. The [Service configuration guide](../../packages/a13n-service/README.md#executable-configuration) owns source precedence and environment names. Relative paths use the TOML file's directory. `var/service/` therefore resolves to the same location regardless of where the executable is invoked.

The development tool validates the *effective* configuration, including environment overrides, before touching stores. It only manages its dedicated loopback database `a13n_service_dev`, Redis database 0, and the exact `var/service/objects` and `var/service/files` roots. Remote databases, different database identities, S3 buckets, alternate directories and symlink redirections are rejected. PostgreSQL and Redis ports may be changed in configuration; the tool derives the Compose port mappings from those same values. The Compose project name is derived from the checkout path, so separate worktrees do not share named volumes. Concurrent worktrees need different host ports and separate Service/model listeners. Langfuse uses its own checkout-derived Compose project and volumes. Its HTTP port comes from `observability.query.langfuse_base_url`; media uses the adjacent port. Both bind only to `127.0.0.1`. Its worker, PostgreSQL, Redis and ClickHouse are internal to that stack. Neither Langfuse nor Service infrastructure reads the root `.env`; optional Harness/debug and live-test workflows remain independent.

A reset recreates owned PostgreSQL/Redis volumes and removes the owned state directory, including object bytes and materialized files. It then replays real migrations and optionally seeds. A failed reset leaves a marker that prevents the local launcher from serving partial state; rerun reset to recover. It does not restore previous manually created content.

Generated files, object content and the non-secret seed resource index `var/service/seed.json` and `var/service/seed-report.md` are ignored by Git. Detailed seed execution logs are stored in the private `var/service/seed.log` file. The database is not a fixture dump: Skill archives are built from source fixtures and uploaded normally, Assets are published and verified through the content API, and Runs produce their real retained items. Fresh randomized identities make browser sessions from before reset invalid. Reload Console after reset to discard its in-memory resource cache.

## Validation

```sh
make dev-state-check
```

The reset tests own disposable containers with separate ports and temporary paths. They do not reset your development state. They cover target isolation, process locking, failed-reset recovery, repeated reset, normal login, object reads, and clearing database, Redis and files together. Service configuration tests cover TOML validation, precedence, secret redaction and path resolution.
