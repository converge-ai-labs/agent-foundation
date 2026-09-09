# Local Service development

This directory owns the local Service environment: its explicit TOML configuration,
PostgreSQL and Redis containers, state reset tooling, and fictional sample content.
It does not depend on Console. Console, SDKs, the service CLI and direct API clients
can all use the resulting Service.

## Daily workflow

These local tools support macOS and Linux. From the repository root, with Docker running:

```sh
make dev-reset STATE=seeded
make service-dev
```

`service-dev` starts dependencies, applies committed migrations, and runs Service
at `http://127.0.0.1:8000` with a scripted local model at
`http://127.0.0.1:18080/v1`. Ctrl+C stops the application and model, preserving data.
`make dev` is the optional outer launcher for Service plus Console. Console's
separate Vite command remains documented in its README.

For Console, sign in as `admin@example.com` with the public test password
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
Runs execute in sequence when sharing a local filesystem Environment. Waiting
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

`SERVICE_CONFIG` selects the same explicit TOML for development and database Make
targets. The [Service configuration guide](../../packages/a13n-service/README.md#executable-configuration)
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
