---
title: Configure Service
description: Load deployment settings, connect shared infrastructure, and tune execution and monitoring.
---

Configure Service in three steps: connect its shared stores, supply persistent encryption keys, then start the required process roles. Settings load once at startup. Restart every affected process after a change. Use this guide for setup decisions and the [settings reference](configuration-reference.md) for individual fields, bounds and defaults.

## Sources and precedence

1. Save deployment settings in a TOML file, such as `service.toml` below.
2. Supply credentials through the process environment or your platform's secret store.
3. Pass `--config service.toml` **before** the command, or set `A13N_SETTINGS_FILE`.
4. Restart the process and check readiness before testing an application request.

`A13N_<SECTION>__<FIELD>` replaces the matching TOML field. For example, `A13N_WORKER__SLOTS=6` overrides `[worker] slots = 2`. Lists, maps and nested sections use a complete JSON value: `A13N_AUTH__MAIL` replaces the mail section rather than merging individual fields. Service reads only the selected file and environment; it does not discover `.env` files or search configuration directories.

This example assumes PostgreSQL, Redis, an S3-compatible bucket and an HTTPS reverse proxy already exist. Replace their addresses and public origin:

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

Set `trusted_proxies` to your actual proxy addresses or CIDRs. Supply the database credential, object-store credentials and [encryption keys](#encryption-keys) before starting. For a single process:

```sh
a13n-service --config service.toml run --role all
```

From another terminal, check the public endpoint, then sign in and send a model-only prompt:

```sh
curl --fail --silent --show-error https://agents.example.com/readyz
```

A ready response confirms process and schema readiness. The prompt checks the configured provider and model path. Unknown settings stop startup. The CLI reports the configuration error type without printing potentially secret input values.

## Required infrastructure

Every role must use the same durable state and decryption keys:

| Configure           | Purpose                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------- |
| `server.public_url` | The Console/API origin, browser-origin checks, mailed links and authorization callbacks. |
| `database.url`      | PostgreSQL durable state using `postgresql+psycopg://`.                                  |
| `redis.url`         | Shared wakeups, live output, rate limits and monitoring coordination.                    |
| `objects.*`         | Checkpoints, displays, uploads, assets, skill packages and images.                       |
| `encryption.*`      | Keys for stored credentials and other protected values.                                  |

### PostgreSQL

Use a database dedicated to Service. Set `database.url` through `A13N_DATABASE__URL` when it contains credentials. Tune connection and statement timeouts for that deployment. For migration ownership and upgrade order, see [schema migrations](operations.md#schema-migrations).

### Redis

Point every process at the same Redis endpoint. Redis accelerates work and transports live session output; PostgreSQL retains durable state. During a Redis outage, stored results remain readable and workers scan for work, while live output and rate limiting are unavailable. Readiness reports `"degraded": ["redis"]`. Temporary environment monitoring also depends on Redis ownership and routing.

### Objects

Choose the backend by how your processes share storage:

- **One host or shared volume:** use `objects.backend = "local"` and `objects.root`. Every process must see the same directory. Relative roots resolve from the process working directory.
- **Multiple hosts:** use `objects.backend = "s3"` and `objects.bucket`, then set `region`, `endpoint_url` and `prefix` as needed. Supply `access_key_id` and `secret_access_key` together, or use the default AWS credential chain.

The store needs ordinary reads, writes, listing and deletes. Service writes each object under a new key. `objects.max_bytes` limits one stored object; `upload_bytes`, `upload_limit` and `upload_window_seconds` control upload size and per-principal rate.

For S3-compatible storage, choose `objects.addressing_style` to match the endpoint:

| Value     | Address form          | Use                                                      |
| --------- | --------------------- | -------------------------------------------------------- |
| `auto`    | Selected by the SDK   | Default behavior.                                        |
| `path`    | `endpoint/bucket/key` | Endpoints that require path-style buckets.               |
| `virtual` | `bucket.endpoint/key` | Bucket-subdomain endpoints, including Alibaba Cloud OSS. |

Virtual addressing requires compatible bucket names, DNS and TLS certificates. When `addressing_style` is omitted, `path_style = true` selects `path` and `false` selects `auto`. Do not combine `path_style = true` with an explicit `auto` or `virtual`. The environment override is `A13N_OBJECTS__ADDRESSING_STYLE`; Helm uses `objects.addressingStyle`.

If an S3-compatible provider rejects optional streaming checksum trailers, set `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` on every Service process. This includes Alibaba Cloud OSS deployments. With Helm, put it in the environment file used for `existingSecret`. Required checksums and Service digest verification remain enabled. Service uses the same plain `PutObject` path across providers.

### Encryption keys

Configure encryption before saving provider credentials, connection tokens, webhook secrets or queued identity mail. Service uses AES-GCM with a named active key. Choose one key source:

- **Single-host key file:** set `encryption.key_file` to persistent storage shared by the processes. On first start, Service creates a missing file with mode `600`; later starts read that same key.
- **Managed key ring:** set `encryption.active_key_id` and `encryption.keys`. Each value is a base64-encoded 32-byte random key. Supply the ring through your secret store, using the same value on every role.

For a new key ring, generate a key once:

```sh
export A13N_ENCRYPTION__ACTIVE_KEY_ID=primary
export A13N_ENCRYPTION__KEYS="{\"primary\": \"$(openssl rand -base64 32)\"}"
```

Persist those values in the deployment secret store. Do not regenerate `primary` on each restart. The key file and explicit ring are mutually exclusive. To migrate a key file into a ring, preserve its content under the ID `key_file`.

To rotate, add a new key under a new ID and make it active. Keep older keys for values already encrypted with them. Back up keys with the database: losing a key makes its encrypted values unreadable. Starting without keys is allowed, but credential writes fail with `unavailable`.

## HTTP server

`server.host` and `server.port` select the listener. `server.public_url` is the origin users reach, which can differ behind a proxy. Use HTTPS for remote access. Either configure `tls_certificate` and `tls_key` on Service or terminate TLS at a trusted reverse proxy.

List only the proxy addresses or CIDRs in `server.trusted_proxies`. Service accepts forwarded client addresses and schemes from those proxies; rate limits use the resulting client address. An HTTPS public URL gives login sessions a `Secure`, `__Host-` cookie. Plain HTTP does not.

Keep request limits separate from execution timeouts. `server.request_bytes` bounds the incoming body (`413 payload_too_large`), and `request_timeout` bounds body arrival (`408 request_timeout`). `readiness_timeout` bounds readiness checks; `shutdown_timeout` bounds HTTP graceful shutdown.

## Identity and mail

Enable SMTP when users need password resets or email changes. Without SMTP, an invitation link is returned once to its creator for manual delivery.

1. Configure an encryption key source so queued links can be encrypted.
2. Set the SMTP host, port, security mode and sender.
3. If the server requires authentication, supply both `smtp_username` and `smtp_password`.
4. Restart the API-serving processes and send an invitation to verify delivery.

```toml
[auth.mail]
smtp_host = "smtp.example.com"
smtp_port = 587
smtp_security = "starttls"
sender = "agents@example.com"
```

For an environment override, provide the **whole** mail object as JSON in `A13N_AUTH__MAIL`, including any credentials. Mail links are never logged. `auth.session_seconds` controls login lifetime, `invitation_seconds` invitation validity, and `link_seconds` password-reset/email-change validity. `login_limit` per `login_window_seconds` applies per client address; public authorization callbacks have a separate budget with those limits.

## Outbound requests

Configure a provider's endpoint and credentials in its workspace resource. Deployment settings determine where Service-owned clients may connect:

- HTTPS is required by default. Permit a specific plaintext origin through `providers.http_origins` when needed.
- URLs carry no user information, fragment or credential-like query parameter. Put credentials in their designated fields.
- Host provider clients refuse redirects and compressed responses, and bound bodies through `providers.response_bytes`.

For example, allow a model server on the Docker host without opening every HTTP destination:

```toml
[providers]
http_origins = ["http://host.docker.internal:11434"]
```

Keep certificate verification enabled. The shared process variable `A13N_OUTBOUND_TLS_VERIFY` accepts `true` (also the unset default) or `false`; other values fail startup. It has no TOML equivalent. A deliberate `false` disables verification for covered owned HTTP clients; read [coverage and interception risks](../a13n-harness/models.md#outbound-tls-verification) before using it.

An application can further restrict one run through `options.configuration`. This is the `options` object in an input request:

```json
{
  "configuration": {
    "allowed_hosts": ["api.example.com", "regex:(api|docs)\\.example\\.com"],
    "extensions": {}
  }
}
```

Include every provider, connection and remote environment hostname that run needs. `null` permits any host; `[]` denies all. Ordinary entries match exact normalized hostnames or IPs; `regex:` patterns match the full hostname. Globs, ports and CIDRs are not host rules. See [host rules](../a13n-harness/context.md#host-rules-and-regular-expressions) for escaping and normalization.

Acceptance freezes this configuration for input URL reads, execution, recovery, resume and children. Steering may omit it or repeat the same value. To change it, submit `delivery: "next_run"`; a different value on an active run produces `run_configuration_immutable`. Console has no controls for this field. Namespaced `extensions` affect only consumers that support them.

Host rules check declared URL names, without DNS prechecks or IP pinning. Management requests outside a run use deployment policy. Enforce restrictions on arbitrary shell, plugin and opaque SDK traffic at the environment or deployment network boundary.

### Outbound proxies

Set standard proxy variables on each process or container that needs them:

```bash
export http_proxy=http://proxy.example.com:8080
export https_proxy=http://proxy.example.com:8080
export no_proxy=localhost,127.0.0.1,::1,.internal.example.com
```

Uppercase forms and `ALL_PROXY` also work; selection and bypass matching follow `httpx2`. An HTTP proxy can carry HTTPS through CONNECT. Host HTTP clients use these routes for models, Remote MCP/OAuth, connectors, memory, web requests, catalogs and webhooks. Host rules and TLS verification still apply, and failed proxy requests do not retry directly.

HTTPS Envd connections use these variables; plaintext local or provider-private Envd HTTP stays direct. Other environment and storage SDKs own their proxy behavior. Allow access to `https://models.dev/catalog.json` for Console's model catalog, or use the last catalog/manual upstream IDs if it is unreachable.

For slow upstreams, tune the matching budget: `providers.model_timeout` covers each read of a model exchange, `tool_call_seconds` a connection tool call, `operation_seconds` authorization steps and resource tests, and `flow_seconds` browser authorization. `discovery_ttl` controls tool-discovery caching. `return_urls` lists exact callback destinations; `mcp_servers` supplies [MCP suggestions](tools.md#mcp-server-suggestions).

## Execution

Begin with `worker.slots`, the concurrent attempts per worker process. Increase it only when provider quotas, target capacity and database connections can support the extra work. Use these groups for other changes:

| Goal                                    | Settings                                                                                             |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Adjust recovery and authority checks    | `worker.max_attempts`, `lease_seconds`, `authority_seconds`.                                         |
| Bound child runs and queued input       | `worker.child_depth`, `child_count`; `control.inbox_count`, `inbox_bytes`.                           |
| Tune live streaming and display storage | `worker.stream_*`, `page_items`, `page_bytes`, `content_bytes`, `output_bytes`, `compression_level`. |
| Tune target preparation and maintenance | `environments.*`.                                                                                    |
| Limit retained tools                    | `tool_executions.max_active`, retention and shutdown settings.                                       |

Retained calls default to 64 active executions per worker and a five-second foreground wait. That wait is the deployment fallback for [execution policy](agents-and-runs.md#execution-policy), not a deadline; agent, thread or single-run choices can override it. Results remain for at most 24 hours after completion. First delivery acknowledgement starts a shorter one-hour reread window without extending the maximum age.

Plan shutdown time for all phases: worker attempts drain, retained calls get `shutdown_grace_seconds` (300 by default), then cancellation gets `finalization_seconds` (10) for cleanup and publication. Unfinished records become lost through lease reconciliation after the process exits. Set the platform's termination grace accordingly; see [operations](operations.md).

In split deployments, set `server.control_url` to a control API address reachable from workers when the public URL is unsuitable. Internal platform MCP uses that address while retaining the public HTTP Host. `plugins.keys` admits installed Harness plugin factories by entry-point key; `composer.models` sets preferred upstream names for [Agent Composer](agent-composer.md).

## Logs, metrics and traces

Start with JSON logs and enable a private metrics listener when needed. `telemetry.log_level` and `log_format` apply to every command. The server can write a rotating JSON file through `log_file`, `log_file_max_mb` and `log_file_backups`; `log_stdout = false` requires a file. `metrics_port` must differ from the HTTP port. Follow [monitoring setup](monitoring.md#metrics) to verify scraping.

Choose one trace backend for export and [trace queries](agents-and-runs.md#traces):

| `telemetry.trace_backend` | Required settings                                          |
| ------------------------- | ---------------------------------------------------------- |
| `none`                    | Default; tracing is off.                                   |
| `langfuse`                | `trace_url`, `langfuse_public_key`, `langfuse_secret_key`. |
| `logfire`                 | `trace_url`, `logfire_write_token`, `logfire_read_token`.  |

Set `trace_content` to `none`, `standard` or `full` based on which prompt, output and tool content may leave the deployment. `trace_query_timeout` limits reads from the backend. See [Harness observation](../a13n-harness/observation.md) for content levels.

## Environment monitoring

`monitoring.enabled` defaults to `true`, but collection starts only after a target selects a data source. For a first chart, bind **Local Cache** using the [environment monitoring guide](environment-monitoring.md#bind-and-verify-an-environment).

For pull collection, configure `monitoring.collectors.vmagent` or `.alloy` before creating a pull source. The map is empty by default. Each entry needs an absolute shared directory and a reload endpoint. Set exactly one `all` or `control` process as `writer = true`; others use the default `false`. Alloy also needs an existing `receiver`. Follow [collector bootstrap](environment-monitoring.md#bootstrap-an-optional-collector) for mounts, credentials, reload policy and verification. Environment overrides use the complete JSON map `A13N_MONITORING__COLLECTORS`.

Once the collector exists, offer an optional preset to named workspaces:

```toml
[monitoring.deployment_preset]
name = "Deployment metrics"
collector = "vmagent"
query_endpoint = "https://metrics.example.com"
workspace_ids = ["ws_example"]
```

An empty list exposes no preset. `A13N_MONITORING__DEPLOYMENT_PRESET` accepts the whole object as JSON. Supply the optional read token separately through `A13N_MONITORING__DEPLOYMENT_QUERY_BEARER_TOKEN`, then restart control processes. Administrators [confirm the preset](environment-monitoring.md#confirm-a-deployment-preset) to create a source and optionally snapshot its credential. Deployment changes do not update existing sources or bindings.

## Nested bounds

When tuning limits, change the related budgets together. Upload size must fit below request size; child output plus its envelope must fit the inbox; authority checks and object operations must fit the lease; worker drain must fit shutdown. Environment scan and renewal budgets must leave time to renew hosted targets.

Startup rejects inconsistent combinations. If a change produces `Invalid Service configuration`, check the changed fields and their containing budgets in the [settings reference](configuration-reference.md).

## Container deployments

The image uses the same executable and settings for all roles. Mount your TOML at the path passed to `--config` (the supplied image uses `/app/service.toml`) and inject credentials into the container environment. Editing a host file affects the container only when it is mounted there. Recreate or restart affected containers after changes; changed environment variables require recreation.

Use the [single-host Compose stack](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose) for `all` with PostgreSQL, Redis and host-Engine Docker environments. Use the [Helm chart](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes) for split control/worker Deployments and a migration Job. `all` and `control` serve Console and the API at `server.public_url`.

## Workspace provisioning

Automatic Local/Docker provider and template creation runs only in the `all` role. Both are disabled in generic settings; the supplied socket-enabled Compose stack enables Docker and leaves Local off. Split control/worker deployments configure execution resources explicitly.

For Docker initialization, grant the Service process access to the intended Engine, through `environments.docker_host` or its Docker environment. A CLI installation alone gives no Engine access. Then enable:

```toml
[provisioning.docker]
enabled = true
```

The default template uses Service's reviewed sandbox image and `pull_policy = "if_missing"`. Provisioning creates resources once; initialization neither pulls an image nor creates a container. The image is pulled when an instance needs it. Later setting changes do not overwrite existing templates: edit those in Console or through the API. Existing instances keep their image.

For a locally built image in a repository checkout:

1. Run `make image-sandbox` from the repository root.
2. Save this override as `compose.local.yaml` in that root:

```yaml
services:
  service:
    environment:
      A13N_PROVISIONING__DOCKER: '{"enabled":true,"image":"a13n-sandbox:local","pull_policy":"never"}'
```

3. Start the stack with both files:

```sh
docker compose -f deploy/docker/compose/a13n-service.yaml -f compose.local.yaml up -d --wait
```

For direct TOML deployment, put the same `image` and `pull_policy` fields in `[provisioning.docker]`. Use `if_missing` for registry images. Environment overrides replace the complete JSON object; the old `A13N_DOCKER_ENVIRONMENT_IMAGE` shortcut is no longer read.

For Local execution in a development deployment only, enable it with an explicit absolute root:

```toml
[provisioning.local]
enabled = true
root = "/srv/a13n/environments"
```

Local executes directly on the worker host without isolation. Disabling it preserves directories and resources but makes Local unavailable for subsequent execution. Docker provisioning disablement leaves manually configured Docker providers usable. See [workspace provisioning](environments.md#workspace-provisioning) for retry and ownership behavior.
