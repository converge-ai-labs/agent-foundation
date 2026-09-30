---
title: Configure Service
description: 'Configure Service from TOML and environment variables: infrastructure, identity, execution, and telemetry.'
---

The Service reads its settings once, at startup, from an optional TOML file and from environment variables. Restart every process after a change. The [settings reference](configuration-reference.md) lists every field with its type, bounds and default.

## Sources and precedence

Select the file with `a13n-service --config service.toml ...` or the `A13N_SETTINGS_FILE` environment variable. Any `A13N_<SECTION>__<FIELD>` variable overrides that field of the file, for example `A13N_DATABASE__URL` for `database.url`. Nothing else is read: there is no automatic `.env` file and no configuration search path.

Unknown settings stop startup rather than falling back to defaults. Validation errors avoid printing secret values.

Every field that takes a list, a map or a nested section, such as `server.trusted_proxies`, the `providers` lists (`private_domains`, `private_cidrs`, `http_origins`, `return_urls`, `mcp_servers`), `encryption.keys`, `plugins.keys` or the nested `auth.mail` section, takes JSON as an environment variable, for example `A13N_PLUGINS__KEYS='["notes"]'` or `A13N_AUTH__MAIL='{"smtp_host": "smtp.example.com", ...}'`.

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

Use `objects.addressing_style` to select `auto` (SDK selection), `path` (`endpoint/bucket/key`) or `virtual` (`bucket.endpoint/key`). Virtual addressing requires compatible bucket names, DNS and TLS certificates; use it for providers such as Alibaba Cloud OSS that require bucket subdomains. When omitted, the existing `path_style` behavior remains: `true` forces `path`, and `false` uses `auto`. An explicit addressing style takes precedence over `path_style=false`; `path_style=true` together with `auto` or `virtual` fails configuration validation. For Helm, set `objects.addressingStyle`; environment overrides use `A13N_OBJECTS__ADDRESSING_STYLE`.

For S3-compatible providers that reject optional streaming checksum trailers, including Alibaba Cloud OSS, set the standard AWS SDK environment variable `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` on every Service process. This works for local, Docker and Kubernetes deployments; with Helm, include it in the environment file used to create `existingSecret`. It leaves checksums required by an operation enabled and does not change Service digest verification. When unset, SDK defaults apply. OSS needs no native write header or bucket-versioning check: the same plain `PutObject` path serves every provider. Remove the earlier experimental `objects.write_mode` / `A13N_OBJECTS__WRITE_MODE` / Helm `objects.writeMode` setting when upgrading from this branch.

### Encryption keys

Provider and connection credentials, OAuth tokens and queued mail links are encrypted with AES-GCM under the active key of the key ring. Each key is 32 random bytes, base64-encoded, under an ID you choose:

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

Identity mail is sent through SMTP when `auth.mail.smtp_host` is set. Without it, invitation links are returned once to the inviter, and password reset and email change are unavailable. Mail links are queued encrypted, so SMTP requires `encryption.active_key_id`. Links are never written to logs.

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
- Private, loopback and link-local destinations are refused unless the host matches `providers.private_domains` (subdomains included) or the resolved address is in `providers.private_cidrs`. Cloud metadata addresses are always refused.
- On direct host-owned HTTP connections, addresses are checked after DNS resolution and only checked addresses are dialed. Redirects are not followed, compressed responses are refused, and response bodies are bounded by `providers.response_bytes`.

### Outbound proxies

Set standard proxy environment variables on each Service process or container that needs outbound access; no `A13N_` prefix or TOML proxy setting is needed:

```bash
export http_proxy=http://proxy.example.com:8080
export https_proxy=http://proxy.example.com:8080
export no_proxy=localhost,127.0.0.1,::1,.internal.example.com
```

Uppercase forms and `ALL_PROXY` are supported; selection and bypass matching follow `httpx2`. An HTTP proxy URL can carry HTTPS traffic through CONNECT. Models, Remote MCP/OAuth, connectors, record memory, web requests, model catalogs, webhooks and other callers of the host HTTP client use these routes.

**The deployment operator's proxy is trusted outbound infrastructure.** Request URL validation and TLS verification remain enabled, but the proxy owns final DNS and destination network restrictions. Application-level DNS/IP pinning and final-address blocking apply to direct connections, including `NO_PROXY`, not to the proxy's outgoing connection. Configure restrictions on the proxy when needed. Existing endpoint prechecks can still require local DNS. A failed proxy request does not silently fall back to direct.

HTTPS Envd attachments also use environment proxies; plaintext local/provider-private Envd links remain direct. Other SDK-owned environment and storage transports retain their own proxy behavior. This does not change the Envd controlled-egress broker or its execution isolation policy.

API-serving processes also read the public model catalog from `https://models.dev/catalog.json`, at most every 60 seconds by default, for the Console's model picker. Without access to it, the catalog is unavailable and models are added by ID.

For example, to use a model server on the Docker host:

```toml
[providers]
private_domains = ["host.docker.internal"]
http_origins = ["http://host.docker.internal:11434"]
```

Other `providers` settings bound provider work: `model_timeout` (each read of one model exchange), `tool_call_seconds` (one connection tool call), `operation_seconds` (authorization steps and resource tests), `discovery_ttl` (cached tool discovery) and `flow_seconds` (how long a browser authorization may take). `providers.return_urls` lists the exact Console URLs a browser authorization may return to, and `providers.mcp_servers` adds [MCP server suggestions](tools.md#mcp-server-suggestions).

## Execution

| Setting                                            | Effect                                                                                                                      |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `worker.slots`                                     | Attempts one worker process runs concurrently.                                                                              |
| `worker.max_attempts`                              | Attempts a run may be charged before it fails.                                                                              |
| `worker.lease_seconds`, `worker.authority_seconds` | How long an attempt's lease lasts, and how often the worker renews it and rechecks cancellation and the principal's access. |
| `worker.drain_seconds`                             | How long a stopping worker waits for its attempts to hand off.                                                              |
| `worker.child_depth`, `worker.child_count`         | Depth and count bounds for subagent runs.                                                                                   |
| `worker.stream_coalesce_seconds`                   | How long consecutive text, reasoning or tool-argument deltas are merged into one live stream event.                         |
| `worker.stream_trim_seconds`                       | How long live stream entries a checkpoint covers stay in Redis, so a briefly disconnected client resumes without a gap.     |
| `worker.stream_length`, `worker.stream_ttl`        | Backstop length cap and idle lifetime of one thread's live stream in Redis.                                                 |
| `worker.display_bytes`, `worker.output_bytes`      | Bounds of a run's display and result.                                                                                       |
| `control.inbox_count`, `control.inbox_bytes`       | Capacity of one thread's inbox (`inbox_bytes` defaults to 2 MiB).                                                           |
| `control.subscriptions`                            | Webhook subscriptions per workspace.                                                                                        |
| `environments.*`                                   | Environment maintenance cadence, provider-call bounds and how long an attempt waits for its environments.                   |

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

Automatic Local and Docker setup runs only in the single-host `all` role. Both default off. For example:

```toml
[provisioning.local]
enabled = true
root = "/srv/a13n/environments"

[provisioning.docker]
enabled = true
```

Local requires an explicit absolute root on the machine running the Service; there is no generic default. It also enables the Local provider type. `make dev` supplies its own checkout path. Replace the retired `environments.allow_local` setting with this Local section. Disabling Local preserves directories and resources, but future execution requires it to be enabled again.

Docker uses the existing operator Engine setting, `environments.docker_host`, or the process Docker environment. Running inside Compose requires access to the Engine, normally the socket mount in the supplied single-host stack. Installing a Docker CLI alone is insufficient. Docker provisioning does not affect manual Docker providers.

Environment overrides use a JSON object for each component, for example `A13N_PROVISIONING__DOCKER='{"enabled":true}'`. The default Docker template pins the GHCR companion image matching the installed Service version and pulls it when an instance is first created if needed. The supplied socket-enabled Compose stack enables Docker and leaves Local off; `A13N_DOCKER_ENVIRONMENT_IMAGE` overrides the initial template's image there.

To use a locally built image, run `make image-docker-environment`, set `image = "a13n-docker-environment:local"` and `pull_policy = "never"`. Settings initialize resources once; change an existing template through Console or the API to change future instances. Existing instances keep their original image. See [automatic local setup](environments.md#automatic-local-setup) for retry and ownership behavior.
