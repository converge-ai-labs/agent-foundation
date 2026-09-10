# Local Service development

This directory owns the local Service environment: its explicit TOML configuration,
PostgreSQL and Redis containers, local Langfuse, state reset tooling, and fictional sample content.
It does not depend on Console. Console, SDKs, the service CLI and direct API clients
can all use the resulting Service.

## Daily workflow

These local tools support macOS and Linux. From the repository root, with Docker running:

```sh
make dev
```

This installs locked dependencies, starts PostgreSQL, Redis and the complete local
Langfuse stack, verifies Langfuse project authentication, applies committed
migrations, and runs Service, the scripted model and Console. No `.env`, manual
Langfuse project creation, API-key copying or OTEL exports are needed.

Use `make setup` to prepare Python dependencies, infrastructure and schema without
starting an application listener. It preserves existing data and credentials.
Use `make service-dev` for Service and the scripted model without Console or its
frontend dependencies. Default addresses are:

| Component      | Address                     |
| -------------- | --------------------------- |
| Console        | `http://127.0.0.1:5173`     |
| Service        | `http://127.0.0.1:8000`     |
| Scripted model | `http://127.0.0.1:18080/v1` |
| Langfuse       | `http://127.0.0.1:3000`     |
| Langfuse media | `http://127.0.0.1:3001`     |

Ctrl+C stops the applications, not the Docker infrastructure. The outer launcher
stops both Service and Console if either exits. Console's separate Vite command
remains documented in its README.

A new installation follows the normal initial administrator invitation flow;
the single-use link is printed in protected Service startup logs when SMTP is
not configured. For fictional accounts and example conversations, explicitly run
`make dev-reset STATE=seeded` **before** starting applications. This deletes existing
Service data; neither setup nor daily startup implicitly resets or seeds it.

After a seeded reset, sign in to Console as `admin@example.com` with the public test password
`local-public-password-123`. `builder@example.com`, `runner@example.com`, and
`viewer@example.com` use the same public test password with different Workspace
roles. All identities and content are fictional. None of these credentials grants
access to any real deployment.

```sh
make dev-down                 # Stop infrastructure; preserve data
make dev-reset STATE=empty    # Rebuild the installation without business data
make dev-reset STATE=seeded   # Rebuild with accounts, resources and history
```

Stop Service before resetting. The launcher holds a local lifecycle lock; reset
also refuses active PostgreSQL or Redis clients started outside that launcher.
Reset never kills unrelated application processes. Dependencies may start during
reset, but no Service listener or Console is left running when reset completes.
The seeded reset temporarily opens the ordinary Service application runtime to
provision through its real identity, resource and execution paths, then closes it.

`empty` contains the migrated schema. The next Service startup issues the normal
initial administrator invitation; without SMTP, its single-use link is printed in
protected startup logs. `seeded` provisions more than 60 Agents, Skills and Assets,
120 bulk Sessions plus distinct execution journeys, and an additional empty Workspace.
It checks the resulting data before reporting success and writes exact counts and
a scenario-to-resource index to `var/service/seed-report.md` and `seed.json`.

The local profile sets `[worker].concurrency = 8`. Bulk Session creation uses
that same limit (or the number of Sessions, if smaller). Each execution slot has
its own Environment and directory under `var/service/files/bulk/slot-NN`; only
successive Runs in the same slot reuse a directory. The slot assignments are
recorded in `seed.json`. Long conversations and dependent retry/feedback journeys
remain sequential. Override the limit with `A13N_SERVICE_WORKER_CONCURRENCY` when
running on a smaller machine; it applies to both seeding and the local Service.
The local database pool keeps up to 60 connections with 20 overflow connections;
Redis permits up to 256 connections, including the blocking control-signal reads
for active Runs. One observer polls the Workspace Run collection for all bulk
slots, sizing each page to the slot count and stopping after finding all pending
Run identities. It waits 100 ms between scans. A slot starts its next
Run only after the previous Run is sealed with the expected outcome. Verification
uses the same Run collection and reads independent Thread collections and complete
transcripts with at most eight concurrent requests, retaining pagination and
relationship checks.

## Local traces

`local.toml` enables tracing with standard input/output content and selects the
local Langfuse query project. The development launcher uses that same project to
initialize Langfuse and configure Service's standard OTLP/HTTP exporter. Content
is retained in local Docker volumes; do not use real private inputs unless you
intend to store them there. Langfuse telemetry is disabled in the Compose file.

The public local Langfuse login is `dev@agent-foundation.local` /
`agent-foundation-local`. The organization and project are `Agent Foundation Local`.
The public test API keys are in `[observability.query]` in `local.toml`.
Changing initialization keys does not rotate keys in existing Langfuse data;
restore the matching configuration or explicitly reset the trace store.

```sh
make langfuse-up       # Start and authenticate only the trace backend
make langfuse-test     # Write a real OTLP trace and query it through the Service adapter
make langfuse-down     # Stop Langfuse, retaining traces
make langfuse-reset    # Delete only Langfuse data, never Service data
```

`make setup` checks web/worker readiness and project authentication; the explicit
smoke test additionally waits for ingestion and verifies input search and trace
detail. Application runtime readiness still does not depend on exporter availability.
Service state resets never delete Langfuse volumes. Seeded resets export their
fictional execution journeys to the same trace store.

The default Service composition does not yet supply a `TraceAccessAuthorizer`.
Trace ingestion and the Langfuse UI work, but Console's Service Trace Query API
remains safely unavailable until the authoritative RunAttempt authorizer is
composed. The smoke test uses a fixture-owned authorizer; it verifies transport,
backend reads and HTTP mapping, not production authorization completeness.

### Upgrading an older local Langfuse stack

Older `make langfuse-up` versions used the shared Compose project
`agent-foundation-langfuse-dev`. The launcher warns if it is still running; it
never stops it automatically. To release its ports without deleting its data:

```sh
docker compose --env-file /dev/null \
  --project-name agent-foundation-langfuse-dev \
  --file dev/observability/langfuse.compose.yaml stop
make setup
```

The new checkout-owned stack has separate volumes. Old traces are not migrated
or deleted; retain the old project and volumes if you need to inspect that history.

### Export environment isolation

The local launcher replaces inherited `OTEL_*` variables with the selected local
profile and announces this when applicable. This prevents an ambient remote
collector or trace-specific endpoint/header override from receiving local content.
Loopback HTTP traffic also bypasses inherited proxies; other destinations retain
their proxy policy. It does not change the calling shell. The production CLI
continues to accept standard OTEL environment settings independently from query
configuration.
To opt out of local Langfuse, set `observability.tracing = false` and
`observability.query.provider = "none"` in the selected TOML.

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

The fixture model and protocol endpoints run only on loopback. No real model
provider, connector account, Slack workspace or OAuth authorization is contacted.
Application Accounts use deliberately fictional credentials and keep reception off.
Connector Providers use the reserved `https://connector.invalid` placeholder;
no discovery or OAuth request is made for their pending connections.

Records get normal creation times and fresh identifiers on every reset. Expired
API-key coverage waits for a short-lived key to expire naturally. The baseline is
reproducible in content and relationships, not a byte-identical database image.
Bulk Sessions execute concurrently in isolated local workspaces. Runs execute in
sequence when sharing a local filesystem Environment. Waiting
client-tool requests and queued input remain available for interaction after startup;
no Run is left accepted or running when reset completes.

The local model returns scripted Markdown in English and Chinese. Send `[long]`
for a long answer, `[slow] [long]` to inspect streaming and scrolling, or `[fail]`
to exercise an intentional model error. The dedicated fixture Agents also support
`[client]`, `[mcp]`, `[mcp-fail]`, `[structured]`, `[structured-invalid]`, and
`[delegate]` for their corresponding scenarios. These directives select fixture
behavior; they are not real model reasoning. Run `python -m dev.service model` if
you start Service through its production CLI and want the fixture process separately.

Transient loading/streaming states must be exercised with a running Service.
Real provider compatibility, external OAuth consent, production observability and
multi-day retention behavior remain separate integration tests. A failed Run that
never produced retained Items is recorded in the coverage report; successful and
waiting Runs are required to expose their retained transcript.

## Configuration and ownership

`local.toml` is a committed **local-only** configuration. All supplied credentials
are public test values; never put a real secret in it. Real credentials belong in
explicit environment overrides or the normal resource credential flow, never in
tracked sample files.

`SERVICE_CONFIG` selects the same explicit TOML for development, database and
Langfuse Make targets. There is no implicit overlay or `.env` loading. For a
custom configuration, pass `make dev SERVICE_CONFIG=dev/service/my-local.toml`;
keep private copies out of Git. Console's port and upstream follow the selected
IAM origin and Service listener. The [Service configuration guide](../../packages/a13n-service/README.md#executable-configuration)
owns source precedence and environment names. Relative paths use the TOML file's
directory. `var/service/` therefore resolves to the same location regardless of
where the executable is invoked.

The development tool validates the *effective* configuration, including environment
overrides, before touching stores. It only manages its dedicated loopback database
`a13n_service_dev`, Redis database 0, and the exact `var/service/objects` and
`var/service/files` roots. Remote databases, different database identities, S3
buckets, alternate directories and symlink redirections are rejected. PostgreSQL
and Redis ports may be changed in configuration; the tool derives the Compose
port mappings from those same values. The Compose project name is derived from
the checkout path, so separate worktrees do not share named volumes. Concurrent
worktrees need different host ports and separate Service/model listeners.
Langfuse uses its own checkout-derived Compose project and volumes. Its HTTP
port comes from `observability.query.langfuse_base_url`; media uses the adjacent
port. Both bind only to `127.0.0.1`. Its worker, PostgreSQL, Redis and ClickHouse
are internal to that stack. Neither Langfuse nor Service infrastructure reads the
root `.env`; optional Harness/debug and live-test workflows remain independent.

A reset recreates owned PostgreSQL/Redis volumes and removes the owned state
directory, including object bytes and materialized files. It then replays real
migrations and optionally seeds. A failed reset leaves a marker that prevents the
local launcher from serving partial state; rerun reset to recover. It does not
restore previous manually created content.

Generated files, object content and the non-secret seed resource index
`var/service/seed.json` and `var/service/seed-report.md` are ignored by Git. Detailed seed execution logs are stored in the private `var/service/seed.log` file. The database is not a fixture dump:
Skill archives are built from source fixtures and uploaded normally, Assets are
published and verified through the content API, and Runs produce their real
retained items. Fresh randomized identities make browser sessions from before
reset invalid. Reload Console after reset to discard its in-memory resource cache.

## Validation

```sh
make dev-state-check
```

The reset tests own disposable containers with separate ports and temporary paths.
They do not reset your development state. They cover target isolation, process
locking, failed-reset recovery, repeated reset, normal login, object reads, and
clearing database, Redis and files together. Service configuration tests cover
TOML validation, precedence, secret redaction and path resolution.
