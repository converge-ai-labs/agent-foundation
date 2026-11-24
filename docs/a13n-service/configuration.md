---
title: Configure Service
description: Configure PostgreSQL, Redis, S3 object storage, encryption key rings, outbound network policies, Kubernetes sandboxes, and telemetry in service.toml.
---

Configure Service by connecting its shared backing stores, supplying persistent encryption keys, and starting your `control` and `worker` roles (or `--role all`). See the [settings reference](configuration-reference.md) for every field and default.

## Sources and precedence

Save settings in `service.toml` and pass `--config service.toml` **before** the subcommand (or set `A13N_SETTINGS_FILE`). Override individual fields via `A13N_<SECTION>__<FIELD>` (for example, `A13N_WORKER__SLOTS=6`); maps, lists, and nested sections take a full JSON value (such as `A13N_AUTH__MAIL`).

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

Start and verify readiness:

```sh
a13n-service --config service.toml run --role all
curl --fail --silent --show-error https://agents.example.com/readyz
```

## Required infrastructure

Every role (`control`, `worker`, `all`) must share the same PostgreSQL, Redis, object store, and encryption keys:

- **PostgreSQL (`database.url`):** Durable state (`postgresql+psycopg://`); see [schema migrations](operations.md#schema-migrations).
- **Redis (`redis.url`):** Live output streams, wakeups, rate limits, and monitoring coordination.
- **Object storage (`objects.*`):** Use `backend = "local"` with a shared `root` on single-host setups, or `backend = "s3"` across hosts (`addressing_style` supports `"auto"`, `"path"`, or `"virtual"` for providers like Alibaba Cloud OSS; set `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` if an S3-compatible store rejects streaming checksum trailers).
- **Encryption keys (`encryption.*`):** AES-GCM protection for stored credentials, tokens, and mail links. Use either a shared `encryption.key_file` (`0600`) or an explicit key ring:

```sh
export A13N_ENCRYPTION__ACTIVE_KEY_ID=primary
export A13N_ENCRYPTION__KEYS="{\"primary\": \"$(openssl rand -base64 32)\"}"
```

To rotate keys, add a new ID to `keys` and switch `active_key_id` while retaining older keys to decrypt existing records.

## HTTP server, identity, and mail

- **HTTP server (`[server]`):** Set `public_url` to your external HTTPS origin and list reverse proxy IPs/CIDRs in `trusted_proxies`. Use `control_url` in split deployments when workers reach `control` over an internal address.
- <span id="identity-and-mail"></span>**Identity and mail (`[auth.mail]`):** Configure SMTP alongside encryption keys to email invitations, password resets, and email-change links:

```toml
[encryption]
key_file = "/var/lib/a13n/encryption.key"

[auth.mail]
smtp_host = "smtp.example.com"
smtp_port = 587
smtp_security = "starttls"
sender = "agents@example.com"
```

## Outbound requests

Service requires HTTPS by default for provider, MCP, and webhook destinations. Explicitly allow trusted internal HTTP origins when needed:

```toml
[providers]
http_origins = ["http://host.docker.internal:11434"]
```

Per-run destination allowlists can be passed via `options.configuration.allowed_hosts` (see [host rules](../a13n-harness/context.md#host-rules-and-regular-expressions)).

### Outbound proxies

Set `http_proxy`, `https_proxy`, and `no_proxy` on each Service process/container for outbound model, MCP, connector, web, and webhook traffic. TLS verification remains enabled unless `A13N_OUTBOUND_TLS_VERIFY=false` is explicitly set for local debugging (see [outbound TLS verification](../a13n-harness/models.md#outbound-tls-verification)).

## Execution

Each `worker` runs up to `worker.slots` concurrent attempts (default `128`, range `1`–`1024`). Tune related limits together in `service.toml`:

- **Retries & leases:** `worker.max_attempts`, `lease_seconds`, `authority_seconds`.
- **Delegation & inbox:** `worker.child_depth`, `child_count`, `control.inbox_count`, `inbox_bytes`.
- **Retained tools & graceful drain:** `tool_executions.max_active` (default `64`), `shutdown_grace_seconds` (`300`), and `finalization_seconds` (`10`).
- **Plugins:** Allowlist installed Harness plugin factories via `plugins.keys`.

## Logs, metrics and traces

Configure `telemetry.log_level`, `log_format` (`json`), optional rotating `log_file`, and a separate `telemetry.metrics_port` for Prometheus scraping (see [monitoring](monitoring.md#metrics)).

| `telemetry.trace_backend` | Required settings                                         |
| ------------------------- | --------------------------------------------------------- |
| `none`                    | Default (tracing disabled)                                |
| `langfuse`                | `trace_url`, `langfuse_public_key`, `langfuse_secret_key` |
| `logfire`                 | `trace_url`, `logfire_write_token`, `logfire_read_token`  |

Set `telemetry.trace_content` (`none`, `standard`, or `full`) to control prompt/tool payload export.

## Environment monitoring

`monitoring.enabled` defaults to `true`. Configure pull collectors under `monitoring.collectors.vmagent` or `.alloy` (see [collector bootstrap](environment-collectors.md#bootstrap-an-optional-collector)) and optionally publish a preset to selected workspaces:

```toml
[monitoring.deployment_preset]
name = "Deployment metrics"
collector = "vmagent"
query_endpoint = "https://metrics.example.com"
workspace_ids = ["ws_example"]
```

## Kubernetes environments

Enable Kubernetes sandbox Pods via Helm (`kubernetesEnvironments.enabled: true`, `namespace: agent-sandboxes`, `allowPlaintextEnvd: true` on a trusted private network) or declare identical cluster profiles on both `control` and `worker`:

```toml title="service.toml"
[environments.kubernetes_clusters.in-cluster]
authentication = "in_cluster"
namespaces = ["agent-sandboxes"]
ownership_id = "production-service"
sandbox_service_account = "agent-sandbox"
allow_plaintext_envd = true
```

For local `kind` clusters outside Kubernetes, use `authentication = "kubeconfig"` with `kubeconfig`, `context`, and `allow_port_forward = true` (`kubectl` required). Then create the workspace provider and template as shown in [Kubernetes sandboxes](environments.md#kubernetes-sandboxes).

## Kubernetes Browsers

Enable managed Chromium Pods in Helm (`kubernetesBrowsers.enabled: true`, `namespace: agent-browsers` on Kubernetes 1.32+ with NetworkPolicy and PVC support), then configure the provider in Console ([browser guide](browsers.md#kubernetes)).

## Related guides

- [Configure workspace provisioning](workspace-provisioning.md)
- [Docker Compose](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose) and [Helm chart](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes) deployments
