---
title: Configure Service
description: 'Configure Service from TOML and environment variables: infrastructure, identity, execution, and telemetry.'
---

The Service reads its settings once, at startup, from an optional TOML file and from environment variables. Restart every process after a change. The [settings reference](configuration-reference.md) lists every field with its type, bounds and default.

## Sources and precedence

Select the file with `a13n-service --config service.toml ...` or the `A13N_SETTINGS_FILE` environment variable. Any `A13N_<SECTION>__<FIELD>` variable overrides that field of the file, for example `A13N_DATABASE__URL` for `database.url`. Nothing else is read: there is no automatic `.env` file and no configuration search path.

Unknown settings stop startup rather than falling back to defaults. Validation errors avoid printing secret values.

The shared process variable `A13N_OUTBOUND_TLS_VERIFY` is also recognized outside the `A13N_<SECTION>__<FIELD>` scheme. Unset or `true` verifies destination certificates; `false` explicitly disables verification for owned HTTP clients. Other values stop startup. It has no TOML field. Set it on every Service process, whatever its role, and restart after changes; see [outbound TLS verification](../a13n-harness/models.md#outbound-tls-verification) for exact coverage, exclusions and interception risks.

Use JSON for list, map and nested-section environment values, for example `A13N_PLUGINS__KEYS='["notes"]'`.

```toml
[server]
host = "0.0.0.0"
port = 8000
public_url = "https://agents.example.com"
trusted_proxies = ["10.0.0.0/8"]

[database]
url = "postgresql+psycopg://a13n_service@db.internal:5432/a13n_service"

[redis]
url = "redis://redis.internal:6379/0"

[objects]
backend = "s3"
bucket = "a13n-service-objects"
region = "us-east-1"

[providers]
return_urls = ["https://agents.example.com/connections/callback"]

[telemetry]
log_format = "json"
```

Supply credentials such as the database password, the encryption key ring and object-store keys as environment variables or through your platform's secret store rather than in the file.

## Required infrastructure

| Setting             | What to provide                                                                                                                                                                                                                                                                  |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `server.public_url` | The origin browsers and API clients use to reach the Service, which serves Console and the API there. The Service accepts browser state changes only from this origin, and uses it in mailed links and authorization callbacks. Its scheme decides whether cookies are `Secure`. |
| `database.url`      | A PostgreSQL URL using the `postgresql+psycopg://` driver. Use a database dedicated to this Service.                                                                                                                                                                             |
| `redis.url`         | A Redis endpoint shared by every process.                                                                                                                                                                                                                                        |
| `objects.*`         | Shared object storage for run checkpoints and displays, uploads, assets, skill packages and images.                                                                                                                                                                              |
| `encryption.*`      | The key ring that encrypts stored credentials, or on a single host a key file.                                                                                                                                                                                                   |

### PostgreSQL

PostgreSQL stores the Service's durable state. Set connection and statement timeouts for your deployment; see [schema migrations](operations.md#schema-migrations) for migration ownership.

### Redis

Redis holds rate-limit counters, worker wakeups and provisional thread-stream events; PostgreSQL retains the durable state. If Redis is unavailable, stored results remain readable and workers scan for work, but live streams are unavailable and rate limits are skipped. Readiness reports `"degraded": ["redis"]`.

### Objects

`objects.backend = "local"` stores objects under `objects.root`; a relative path resolves from the working directory. Every Service process must see the same directory, so use it for a single host or a shared volume. `objects.backend = "s3"` uses an S3-compatible bucket: set `bucket`, and as needed `prefix`, `region`, `endpoint_url` and `path_style` (for stores that address buckets by path, such as MinIO). Without `access_key_id` and `secret_access_key`, the default AWS credential chain applies. The Service writes every object once under a new key, so the store needs only plain reads, writes, listing and deletes.

`objects.max_bytes` bounds one stored object. `objects.upload_bytes` bounds one upload, and `upload_limit` per `upload_window_seconds` bounds uploads per principal.

Use `objects.addressing_style` to select `auto` (SDK selection), `path` (`endpoint/bucket/key`) or `virtual` (`bucket.endpoint/key`). Virtual addressing requires compatible bucket names, DNS and TLS certificates; use it for providers such as Alibaba Cloud OSS that require bucket subdomains. When omitted, the existing `path_style` behavior remains: `true` forces `path`, and `false` uses `auto`. An explicit addressing style takes precedence over `path_style=false`; `path_style=true` together with `auto` or `virtual` fails configuration validation. For Helm, set `objects.addressingStyle`; the environment variable is `A13N_OBJECTS__ADDRESSING_STYLE`.

For S3-compatible providers that reject optional streaming checksum trailers, including Alibaba Cloud OSS, set the standard AWS SDK environment variable `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` on every Service process. This works for local, Docker and Kubernetes deployments; with Helm, include it in the environment file used to create `existingSecret`. It leaves checksums required by an operation enabled and does not change Service digest verification. When unset, SDK defaults apply. OSS needs no native write header or bucket-versioning check: the same plain `PutObject` path serves every provider.

### Encryption keys

Provider and connection credentials, model provider extra headers, external target tokens, OAuth tokens, client secrets and pending authorizations, webhook signing secrets and delivery targets, and queued mail links are encrypted with AES-GCM under the active key of the key ring. Each key is 32 random bytes, base64-encoded, under an ID you choose:

```sh
export A13N_ENCRYPTION__ACTIVE_KEY_ID=primary
export A13N_ENCRYPTION__KEYS="{\"primary\": \"$(openssl rand -base64 32)\"}"
```

On a single host, `encryption.key_file` can replace `active_key_id` and `keys`: the Service reads one key from that file and, when the file is missing, generates it there on first start with mode `600`. Put the file on persistent storage every Service process shares, such as the [single-host stack](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose)'s data volume. The key's ID is `key_file`; to move to a key ring later, for example to rotate, add the file's content to `encryption.keys` under that ID.

Without any key the Service starts, but storing any credential fails with `unavailable`. To rotate, add a new key to `encryption.keys` and make it active; keep the old keys in the ring, because values written under them are still read with their original key. Losing a key makes the values encrypted under it unreadable, so back up the key ring with the database.

## HTTP server

The Service serves HTTP on `server.host` and `server.port`. Serve HTTPS directly with `server.tls_certificate` and `server.tls_key`, or terminate TLS at a trusted proxy. When `server.public_url` is HTTPS, browser login sessions use a `__Host-` cookie marked `Secure`; over plain HTTP the cookie is neither, so serve anything reachable beyond a trusted network over HTTPS.

Behind a proxy, list the proxy addresses or CIDRs in `server.trusted_proxies`. The Service then takes the client address and scheme from the `X-Forwarded-For` and `X-Forwarded-Proto` headers of those proxies only; rate limits key on that client address.

`server.request_bytes` bounds a request body (`413 payload_too_large`), and `server.request_timeout` bounds how long the body may take to arrive (`408 request_timeout`). `readiness_timeout` bounds each readiness check, and `shutdown_timeout` bounds graceful shutdown.

## Identity and mail

`auth.session_seconds` is the lifetime of a browser login session. `auth.login_limit` per `auth.login_window_seconds` limits password logins per client address; the public connection-authorization callback has the same limit in its own budget. `auth.invitation_seconds` is how long an invitation stays acceptable, and `auth.link_seconds` how long a password-reset or email-change link stays valid.

Identity mail is sent through SMTP when `auth.mail.smtp_host` is set. Without it, invitation links are returned once to the inviter, and password reset and email change are unavailable. Mail links are queued encrypted, so SMTP requires an encryption key: `encryption.active_key_id` with `encryption.keys`, or `encryption.key_file`. Links are never written to logs.

```toml
[auth.mail]
smtp_host = "smtp.example.com"
smtp_port = 587
smtp_security = "starttls"
sender = "agents@example.com"

[encryption]
active_key_id = "primary"  # the key itself comes from A13N_ENCRYPTION__KEYS
```

Supply `smtp_username` and `smtp_password` together. As an environment variable, pass the whole section as JSON in `A13N_AUTH__MAIL`.

## Outbound requests

Every request the Service makes to a provider, a remote MCP server, an OAuth server or a webhook endpoint passes one endpoint policy:

- URLs use `http` or `https` and carry no user information, fragment or credential-like query parameter.
- With `providers.require_https = true` (the default), plain HTTP is refused except for the exact origins listed in `providers.http_origins`.
- Host provider clients do not follow redirects, refuse compressed responses and bound response bodies with `providers.response_bytes`. TLS verification, credential rules and deadlines remain enabled.

A message submitted to a thread accepts `options.configuration`, separately from the agent revision override:

```json
{
  "configuration": {
    "allowed_hosts": ["api.example.com", "regex:(api|docs)\\.example\\.com"],
    "extensions": {}
  }
}
```

Set `allowed_hosts` to every provider, connection and remote environment hostname the run needs:

- `null` leaves hosts unrestricted; `[]` denies all.
- Ordinary entries match exact normalized hostnames or IPs. `regex:<pattern>` uses Python full-string matching; the example allows only `api.example.com` or `docs.example.com`.
- Invalid or empty patterns fail acceptance. Globs, ports and CIDRs are not host rules. See [host rules and regular expressions](../a13n-harness/context.md#host-rules-and-regular-expressions) for normalization and escaping.

Acceptance freezes the configuration for input URL reads, execution, recovery, resume and children. Steering can omit it or repeat the same value. To change it, submit `delivery: "next_run"`; an active run rejects a different value with `run_configuration_immutable`.

Set this configuration through the API; Console has no controls for it. Namespaced `extensions` are read only by consumers that explicitly support them.

The `allowed_hosts` check compares declared URL hostnames only: it does no DNS precheck, address classification or IP pinning. Management operations outside a run retain process URL/HTTPS policy, not a run's configuration. Enforce network restrictions for arbitrary shell, third-party plugin and opaque SDK traffic at the deployment or environment boundary.

### Outbound proxies

Set standard proxy environment variables on each Service process or container that needs outbound access; no `A13N_` prefix or TOML proxy setting is needed:

```bash
export http_proxy=http://proxy.example.com:8080
export https_proxy=http://proxy.example.com:8080
export no_proxy=localhost,127.0.0.1,::1,.internal.example.com
```

Uppercase forms and `ALL_PROXY` are supported; selection and bypass matching follow `httpx2`. An HTTP proxy URL can carry HTTPS traffic through CONNECT. Models, Remote MCP/OAuth, connectors, record memory, web requests, model catalogs, webhooks and other callers of the host HTTP client use these routes.

Host rules and TLS verification still apply through the operator's proxy. The proxy or deployment network controls destination access. A failed proxy request does not fall back to direct.

HTTPS connections to `a13n-envd` use these proxy variables; plain-HTTP local or provider-private Envd connections stay direct. Other environment and storage SDKs keep their own proxy behavior.

Console's model picker uses `https://models.dev/catalog.json`. If it is unreachable, Service uses its last catalog or lets you add models by upstream ID.

For example, to allow plain HTTP to a model server on the Docker host with `providers.http_origins`:

```toml
[providers]
http_origins = ["http://host.docker.internal:11434"]
```

Other `providers` settings bound provider work: `model_timeout` (each read of one model exchange), `tool_call_seconds` (one connection tool call), `operation_seconds` (authorization steps and resource tests), `discovery_ttl` (cached tool discovery) and `flow_seconds` (how long a browser authorization may take). `providers.return_urls` lists the exact Console URLs a browser authorization may return to, and `providers.mcp_servers` adds [MCP server suggestions](tools.md#mcp-server-suggestions).

## Execution

| Setting                                            | Effect                                                                                                                          |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `worker.slots`                                     | Attempts one worker process runs concurrently.                                                                                  |
| `worker.max_attempts`                              | Attempts a run may be charged before it fails.                                                                                  |
| `worker.lease_seconds`, `worker.authority_seconds` | How long an attempt's lease lasts, and how often the worker renews it and rechecks cancellation and the principal's access.     |
| `worker.drain_seconds`                             | How long a stopping worker waits for its attempts to hand off.                                                                  |
| `worker.child_depth`, `worker.child_count`         | Depth and count bounds for subagent runs.                                                                                       |
| `worker.stream_coalesce_seconds`                   | How long consecutive text, reasoning or tool-argument deltas are merged into one live stream event.                             |
| `worker.stream_trim_seconds`                       | How long live stream entries a checkpoint covers stay in Redis, so a briefly disconnected client resumes without a gap.         |
| `worker.stream_length`, `worker.stream_ttl`        | Backstop length cap and idle lifetime of one thread's live stream in Redis.                                                     |
| `worker.output_bytes`                              | Bound of a run's result.                                                                                                        |
| `worker.page_items`, `worker.page_bytes`           | Size of one page of a run's display history: up to `page_items` items, or fewer that reach `page_bytes`.                        |
| `worker.content_bytes`                             | Images, documents and other binary content larger than this are saved once as their own objects instead of in every checkpoint. |
| `worker.compression_level`                         | zstd level of the run objects a worker writes.                                                                                  |
| `control.inbox_count`, `control.inbox_bytes`       | Capacity of one thread's inbox (`inbox_bytes` defaults to 2 MiB).                                                               |
| `control.subscriptions`                            | Webhook subscriptions per workspace.                                                                                            |
| `environments.*`                                   | Environment maintenance cadence, provider-call bounds and how long an attempt waits for its environments.                       |

`provisioning.local.enabled = true` offers the `local` environment provider, which runs commands directly on the worker host with no isolation. Use it only for development.

`plugins.keys` lists installed Harness plugin factories, by entry-point key, that agents may select. `composer.models` lists preferred upstream model names for the [Agent Composer](agent-composer.md).

## Logs, metrics and traces

`telemetry.log_level` and `telemetry.log_format` (`json`, the default, or `pretty`) apply to every command. `a13n-service run` can also write a rotating JSON file (`log_file`, `log_file_max_mb`, `log_file_backups`), and with `log_stdout = false` only the file. `telemetry.metrics_port` serves Prometheus metrics on a port of its own. [Monitor and troubleshoot](monitoring.md) describes what each signal records.

The Service exports the Harness spans of every attempt to one trace backend and reads the same backend for [trace queries](agents-and-runs.md#traces). Choose it with `telemetry.trace_backend`:

| Backend          | Settings                                                                                              |
| ---------------- | ----------------------------------------------------------------------------------------------------- |
| `none` (default) | Nothing is exported, and trace queries report no backend.                                             |
| `langfuse`       | `trace_url` (such as `https://cloud.langfuse.com`), `langfuse_public_key`, `langfuse_secret_key`.     |
| `logfire`        | `trace_url` (such as `https://logfire-us.pydantic.dev`), `logfire_write_token`, `logfire_read_token`. |

`telemetry.trace_content` (`none`, `standard` or `full`) chooses how much prompt, output and tool content leaves the deployment with the spans; see [Harness observation](../a13n-harness/observation.md). `trace_query_timeout` bounds one backend query.

## Nested bounds

Startup also validates related limits together: leases must cover authority and object-store calls; shutdown must cover worker drain; request and inbox budgets must fit uploads and child output; environment scans must renew hosted sandboxes in time. If you change these limits, check the startup validation error before widening individual timeouts.

## Container deployments

The `a13n-service` image runs every role through the same `a13n-service` entry point. Mount the configuration file (the provided deployments use `/app/service.toml`) and supply credentials as environment variables. The `all` and `control` roles also serve Console, so browsers and API clients share one origin; set `server.public_url` to it.

- The [single-host Compose stack](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose) runs `run --role all` with PostgreSQL, Redis, the Console and Docker environments through the host Engine.
- The [Helm chart](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes) runs control and worker Deployments after a migration Job per release revision, with values for a local kind cluster.

## Workspace provisioning

Automatic setup of the Local and Docker environment providers and their templates runs only in the single-host `all` role. Both default off. For example:

```toml
[provisioning.local]
enabled = true
root = "/srv/a13n/environments"

[provisioning.docker]
enabled = true
```

Local requires an explicit absolute root on the machine running the Service; there is no generic default. It also enables the Local provider type. `make dev` supplies its own checkout path. Disabling Local keeps its directories, provider and templates, but runs cannot use Local environments until you enable Local again.

Docker uses the existing operator Engine setting, `environments.docker_host`, or the process Docker environment. Running inside Compose requires access to the Engine, normally the socket mount in the supplied single-host stack. Installing a Docker CLI alone is insufficient. Docker provisioning does not affect manual Docker providers.

Environment variables take a JSON object for each nested section, for example `A13N_PROVISIONING__DOCKER='{"enabled":true}'`. The default Docker template pins the independently versioned GHCR sandbox image reviewed by Service and pulls it when an instance is first created if needed. The supplied socket-enabled Compose stack enables Docker and leaves Local off; `A13N_DOCKER_ENVIRONMENT_IMAGE` overrides the initial template's image there.

To use a locally built image, run `make image-sandbox`. Then set `image = "a13n-sandbox:local"` and `pull_policy = "never"` in `[provisioning.docker]`. Settings initialize resources once; change an existing template through Console or the API to change future instances. Existing instances keep their original image. See [workspace provisioning](environments.md#workspace-provisioning) for retry and ownership behavior.
