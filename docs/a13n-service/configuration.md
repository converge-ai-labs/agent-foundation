# Configure Service

Service uses one explicitly selected TOML file and typed startup settings. This is a different configuration format from Harness UI's YAML resources and from the code-first Harness SDK.

## Select and validate a file

```console
a13n-service --config /absolute/path/to/service.toml config check
a13n-service --config /absolute/path/to/service.toml serve
```

Precedence, from lowest to highest:

1. Package defaults.
2. The selected TOML file.
3. Recognized `A13N_SERVICE_*` environment variables.
4. Explicit `serve --host` and `serve --role` overrides.

Service does not search for `.env` or another configuration file. Unknown TOML fields fail even when a higher-priority input would replace them. Environment names are enumerated from the current settings model; old `FOUNDATION_*` names do not configure this service.

Settings are immutable after startup. Restart the process after a change. `config check` validates configuration and derived storage/identity settings without starting Service; it is not proof of database, model, SMTP, or cloud connectivity.

## A single-process local profile

This example selects SQLite, process-local Redis emulation, and local object storage:

```toml
[service]
host = "127.0.0.1"
port = 8000
role = "all"

[database]
backend = "sqlite"
sqlite_path = "var/a13n-service.sqlite3"

[redis]
backend = "memory"

[objects]
backend = "local"
local_root = "var/objects"

[filesystem]
root = "var/files"

[migration]
auto_migrate = true

[iam]
public_origin = "http://127.0.0.1:8000"
```

This configures infrastructure, not a ready-to-use account or model. Set up [identity](identity.md), [Models](models.md), and required credential encryption separately. For a complete repository development environment with fictional users and the Console, use the [local development guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/dev/service/README.md) rather than inventing production credentials.

Relative `database.sqlite_path`, `objects.local_root`, and `filesystem.root` paths resolve from the selected file's directory, including values supplied through environment overrides. Without a file, they resolve from the invocation directory. SQLite `:memory:` retains its special meaning. Keep local object and filesystem roots separate.

SQLite and local objects are single-process choices. Do not put SQLite on NFS or use the local object adapter for multiple writing processes. A network backend failure never falls back to local storage.

## Distributed storage

Choose PostgreSQL, Redis, and S3-compatible objects for a distributed deployment. Database and Redis URLs are credential-bearing settings. S3 uses its standard credential chain; provide bucket, region, and only the endpoint/path-style overrides your storage requires.

```toml
[service]
role = "control"

[database]
backend = "postgresql"

[redis]
backend = "redis"

[objects]
backend = "s3"
bucket = "your-private-agent-objects"
region = "us-east-1"

[filesystem]
root = "/mnt/a13n-service-files"
```

Supply actual URLs and secrets through protected deployment inputs. The filesystem mount must exist as required by deployment; Service does not provision NFS. Object compatibility, including atomic conditional deletion and fresh object versions, is checked at startup. A grace period cannot compensate for missing object-store guarantees.

## Environment variable mapping

The [complete field reference](configuration-reference.md) lists every setting, environment variable, default, and field-level constraint from the actual loader.

Most fields use the uppercase section and name, but these mappings are intentionally retained:

| TOML section / field                                           | Environment spelling                                                             |
| -------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `service.host`, `service.port`, `service.role`                 | `A13N_SERVICE_HOST`, `A13N_SERVICE_PORT`, `A13N_SERVICE_ROLE`                    |
| `service.name`, `service.instance_id`                          | `A13N_SERVICE_SERVICE_NAME`, `A13N_SERVICE_SERVICE_INSTANCE_ID`                  |
| `objects`, `assets`, `models`, `environments`                  | Singular `OBJECT_`, `ASSET_`, `MODEL_`, `ENVIRONMENT_` prefixes                  |
| `plugins`, `subagents`, `webhooks`, `hooks`, `runs`, `secrets` | Singular `PLUGIN_`, `SUBAGENT_`, `WEBHOOK_`, `HOOK_`, `RUN_`, `SECRET_` prefixes |
| `logging`                                                      | `LOG_` prefix                                                                    |
| `migration.auto_migrate`                                       | `A13N_SERVICE_AUTO_MIGRATE`                                                      |
| `gateway.a2a_*`                                                | `A13N_SERVICE_A2A_*`, without `GATEWAY_`                                         |
| `observability.query.*`                                        | `A13N_SERVICE_OBSERVABILITY_QUERY_*`                                             |

Arrays are TOML arrays in the file and JSON arrays in environment variables. Standard `OTEL_*` inputs configure telemetry transport independently; they are not aliases for every Service setting.

## Trace deployment environment

Set `[service].deployment_environment_name = "local"` for local execution;
the repository's `dev/service/local.toml` already declares this explicitly.
Service exports the value as the OpenTelemetry resource attribute
`deployment.environment.name`, used by Langfuse's environment filter. This label
is independent of the Run's execution Environment or provider. Restart Service
after changing it; existing traces retain their original labels. The dev launcher
also derives `OTEL_RESOURCE_ATTRIBUTES` from this setting for its OTLP profile.

## Reading execution traces

Service uses one `a13n.service.run_attempt` root per worker Attempt, with the existing Harness and model/tool spans beneath it. Root attributes identify the Run and Attempt, the stored recovery reason, and the final durable outcome and safe failure code. A retry starts a new trace, not a continuation of an unbounded Thread trace.

Three coarse Service spans explain time outside model execution:

- `a13n.service.reconstruct`: dependency checks and invocation preparation, ending before Harness starts.
- `a13n.service.environment.prepare`: actual Environment creation, connection, or recovery. For `on_use`, this appears only on first use or recovery; an unused lazy Environment produces no preparation span.
- `a13n.service.persist`: final state/result publication and the Attempt decision, including saved-outcome recovery and failure publication. A successful Harness can still be followed by failed persistence.

Each phase records its local outcome; failures include an exception class, not raw exception text. Eager Environment preparation overlaps reconstruction, so do not sum all phase durations. Use the root's durable outcome to decide whether the Attempt succeeded.

At `standard` or `full`, the root's `input.value` contains the accepted Run input, including external payloads once ordinary preparation reads them. `output.value` contains the final user-visible Run output only after persistence is confirmed for this Attempt, not merely the last model response. Waiting, continuing, failed, cancelled, and yielded Attempts have no final output. Text and JSON retain their values; system prompts and history are not copied into the root.

Inspect `a13n.run_attempt.input.capture` and `a13n.run_attempt.output.capture` when a value is absent: `content_disabled` means policy suppressed it, `external_payload` means no matching body was available locally, and `not_committed` means this Attempt has no confirmed final output. `unavailable` denotes missing input at the observation boundary. External output already read for ordinary integrity verification, including recovery, is reused after its digest matches the committed reference. No payload is fetched just for tracing. Read the Run resource for the authoritative result.

These diagnostics remain available at `observability.trace_content="none"` when tracing and export are configured. This setting suppresses ordinary execution payload capture, but is not a guarantee that upstream model/tool instrumentation is secret-free.

## Roles and migration authority

| Role           | Responsibility                                               | Schema behavior                                                             |
| -------------- | ------------------------------------------------------------ | --------------------------------------------------------------------------- |
| `all`          | Control, Worker, and Connectivity in one process             | Auto-upgrade only when `migration.auto_migrate=true`; otherwise check heads |
| `control`      | Native APIs and control reconciliation                       | Same conditional auto-upgrade                                               |
| `worker`       | Run execution, Environment maintenance, lifecycle projection | Check only; never migrate                                                   |
| `connectivity` | Provider ingress and durable admission                       | Check only; never migrate                                                   |

Auto-migration defaults to **false**. Coordinate schema preparation before admitting replicas. A dedicated migration job should use the same artifact and configuration while replicas keep auto-migration disabled. PostgreSQL migration locking and timeouts are bounded.

The operator commands include `db upgrade`, `db current --check-heads`, `db history`, and explicit downgrade. Downgrade is potentially destructive; do not run it merely to fix readiness. New migrations belong to the repository's generated/reviewed migration workflow, not handwritten production SQL.

The container's default command and healthcheck both select `/app/service.toml`. If you replace that path in the command, also update the healthcheck to use the same file with `config healthcheck`.

## Required cross-field rules

Field bounds are not the whole contract. In addition:

- PostgreSQL and Redis backends require their corresponding URLs; S3 requires a bucket.
- Webhook claim lease must exceed request timeout; maximum retry delay must cover the base delay.
- Connectivity account pending counts/bytes cannot exceed Workspace bounds. Batch counts/bytes cannot exceed account bounds. Admission lease must exceed its poll interval, and total timeout cannot be shorter than connect/read phase timeouts.
- Credential encryption requires an exact 32-byte standard-base64 master key and a non-empty key identifier. Configure them before creating credential-bearing Providers/connections. Keys are deployment authority, not database content.
- OAuth callback origins and endpoint allowlists remain explicit. Allowing a private destination does not remove provider authentication.
- Enabling trace querying needs both an installed query adapter and a trusted access authorizer. Export credentials alone do not authorize trace reads.

Derived storage, identity, endpoint, and artifact checks can reject values beyond the field-level JSON schema. Read startup errors and readiness rather than treating a successful parse as a healthy deployment.

## Inspect and operate

The process/operator CLI is `a13n-service`, not `a13n-service-cli`. It provides `serve`, `config check`, `config healthcheck`, database commands, and `iam reissue-bootstrap`. IAM/bootstrap and database commands mutate state; use them only for the corresponding operator task.

### Operator command reference

Prefix commands with `a13n-service --config PATH` to select the deployment file. Global `--help` and `--version` do not start Service. Each command also supports `--help`.

| Command                 | Options / arguments                                   | Effect                                                                                                       |
| ----------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `serve`                 | `--host`, `--role all\|control\|worker\|connectivity` | Prepare/check the database and start the selected process; port comes from settings, not a `--port` flag     |
| `config check`          | None                                                  | Validate selected settings without startup or connectivity probes                                            |
| `config healthcheck`    | None                                                  | Request the configured process's `/healthz` with a two-second HTTP timeout; not a readiness or provider test |
| `db upgrade`            | `--revision`, default `head`                          | Apply migrations; mutates the selected database                                                              |
| `db downgrade`          | `--revision`, default `-1`                            | Downgrade; potentially destructive                                                                           |
| `db current`            | `--check-heads`                                       | Inspect revision and optionally fail on unapplied heads                                                      |
| `db history`            | None                                                  | Inspect migration history                                                                                    |
| `db migrate`            | Required `MESSAGE`                                    | Generate a revision; repository contributors use the owning `make db-migrate` workflow                       |
| `iam reissue-bootstrap` | None                                                  | Invalidate and replace a pending administrator invitation; does not reopen completed initialization          |

`GET /healthz` and `GET /readyz` are operational probes. Native schema/docs are under `/api/openapi.json`, `/api/docs`, and `/api/redoc` on control-capable processes. Worker-only and connectivity-only roles do not expose the Native product API.

See [Background tasks](background-tasks.md) for periodic work and retention, [HTTP contracts](http-contracts.md) for client behavior, and [the generated reference](configuration-reference.md) for exact knobs.
