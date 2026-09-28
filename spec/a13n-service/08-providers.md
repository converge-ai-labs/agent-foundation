# Providers: what the Service calls

A provider is a backend the Service calls on a tenant's behalf: a model API, an environment backend, a connector platform, a web search or scrape service, a record memory backend, a Remote MCP server or the trace backend. This chapter owns the provider definitions a deployment registers, the capability flags and type descriptions clients read, runtime handles, the outbound endpoint policy, tool sources, the MCP server catalogue, the model catalog, trace backends and installed Harness plugins. Tenant-configured provider accounts are [provider resources](04-resources.md#provider-resources).

## Terms

- A **provider resource** is a tenant-configured account row under `resources/`: identity, scope, `type`, `config`, write-only credential and enabled state ([04](04-resources.md#provider-resources)).
- A **provider definition** is the Harness's immutable description of one backend type: configuration and credential schemas, authentication, capability flags and the factory that builds a handle. The Service registers the Harness definitions, or its own qualification of one (Docker under the operator's choices, E2B fixed to its cloud, and `local`), so resource validation and execution use the same schemas; it keeps no second implementation catalogue.
- A **provider handle** is the runtime object a factory builds from plain values and a revealed credential for one use. Its caller owns its lifetime and closes it.

## Registry

`providers/registry.py` holds the definitions a deployment registers, keyed by `(kind, type)`. `kind` is `model`, `environment`, `connector`, `web` or `memory`; there is no trace kind.

- `Registry.of(definitions)` groups the distribution's definitions by kind. A duplicate type within a kind, or a definition of any other kind, fails assembly ([09](09-runtime.md#assembly)).
- `get(kind, type)` returns the definition a resource's `type` selects. An unregistered type is `unavailable` with dependency `{kind}:{type}`, so a row whose type a deployment no longer registers stays readable and fails only when used.
- `types(kind)` lists the registered definitions sorted by type.
- `check_model_settings` checks an agent's native model settings against the calling API's schema ([provider type descriptions](#provider-type-descriptions)); settings for a calling API the deployment no longer offers are `unavailable` with dependency `model_api:{api}`; the module function `web_operations` names the operations a web type serves.
- `check_environment_endpoint` is the [environment provider](#environment-providers) rule.

The built-in distribution registers the Harness built-in model, web and memory providers, the built-in connector provider (Composio) and eight environment types. Templates create instances of every one; external envd targets are no provider type ([06](06-environments.md#external-targets)).

| Environment type | Instances                                                                                   | Stop                                 | Destroy |
| ---------------- | ------------------------------------------------------------------------------------------- | ------------------------------------ | ------- |
| `docker`         | containers in the operator's Docker engine, or a remote engine the provider account names   | stops the container                  | yes     |
| `e2b`            | E2B sandboxes in the E2B cloud                                                              | pauses, keeping memory and files     | yes     |
| `daytona`        | Daytona sandboxes of the account's organization                                             | stops, keeping files                 | yes     |
| `modal`          | Modal sandboxes of the account's workspace and app                                          | snapshots the files, then terminates | yes     |
| `vercel`         | persistent Vercel sandboxes of the account's team and project                               | stops, keeping files                 | yes     |
| `sprites`        | Fly.io Sprites of the account's organization, which sleep on their own and wake on use      | unsupported                          | yes     |
| `runloop`        | Runloop devboxes of the account's organization                                              | suspends, keeping files              | yes     |
| `local`          | development only: one directory per instance on the worker host, with no isolation boundary | nothing to stop                      | yes     |

**Docker is operator trust**, since an engine runs containers as root on its host. An account that names no engine uses the operator's `environments.docker_host`, by default the Service process's own Docker environment; an account may name only a remote `tcp://host:port` or `https://host:port` engine, which the [endpoint policy](#outbound-endpoint-policy) checks before every dial. A recipe binds host directories only below `environments.docker_mount_roots`, and none by default; the recipe offers no privileged mode, capabilities, devices, host namespaces, security options, volumes or runtime selection, and cannot raise the worker-side buffers (`max_file_bytes`, `max_output_bytes_per_stream`, `max_spool_bytes`, `max_output_preview_bytes`, `max_query_entries`, `max_concurrent_processes`) above the Harness defaults. Container CPU, memory and process limits remain operator trust: a deployment that needs them bounded gives tenants a dedicated engine through `environments.docker_host`. Changing `environments.docker_host` changes the backend identity of every account that names no engine, so their instances are refused with `provider_identity_changed` ([06](06-environments.md#execution)). Creating a template, or changing its provider or recipe, needs `write` on the provider ([04](04-resources.md#environment-templates)).

**Hosted types reach fixed vendor APIs.** An `e2b`, `daytona`, `modal`, `vercel`, `sprites` or `runloop` account holds the vendor credential and names at most an organization, team, workspace or app, never a host, so no tenant value chooses where the Service connects. The Service's `e2b` type leaves out the Harness's `domain` and `api_url`, which would name the hosts its SDK dials: an account always reaches the E2B cloud, and one that sets them is `invalid_argument` on `config`. Each hosted type names or tags its target with the environment ID, so a continued create finds the target the interrupted one made, and stop and destroy observe the target's state and succeed on one already stopped or gone ([06](06-environments.md#one-outstanding-external-operation)). The `e2b`, `modal`, `vercel` and `runloop` definitions set `requires_keepalive`, because their vendors end sandboxes that are not renewed, and the renewal sweep renews their ready instances before the expiry the provider reports ([06](06-environments.md#renewal)). E2B's timeout and Runloop's idle timer are extended, so the template's idle policy decides when those sandboxes stop. A renewal extends an E2B sandbox by at most its recipe's `timeout_seconds`, so the Service's `e2b` recipe requires at least 300 seconds, the renewal horizon. Renewal cannot pass a vendor's hard lifetime: Modal terminates a sandbox at its recipe's `timeout_seconds`, at most 24 hours, after which the instance is `environment_lost` until it is deleted, and Vercel ends a session at its recipe's `timeout_seconds`, after which the instance is `stopped` and the next run resumes the sandbox. An idle stop that comes first keeps the files.

`local` is registered only when `environments.allow_local` is set, and every process that executes runs must then share the host holding the directories.

Memory Providers back record memories only ([11](11-memory.md#memory-providers)); a definition declares no memory kind. The built-in types are `mem0_platform`, the hosted mem0 Platform, and `mem0_oss`, a self-hosted mem0 REST server the account's `base_url` names; both keep a memory's records under its namespace as the mem0 `user_id` ([Harness: mem0](../a13n-harness/21a-record-memory.md#mem0)). File memories are stored by the Service itself: `postgres` is no provider type.

Connections use the registry through `type` too: `mcp` is the built-in Remote MCP tool source and needs no provider resource, and every other connection type is served by a connector provider resource of that type ([04](04-resources.md#connections)).

## Provider type descriptions

`GET /api/v1/provider-types/{kind}` describes the registered types of one kind to any signed-in principal. Every type reports:

| Field                                       | Meaning                                                                                                     |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `type`, `display_name`                      | the selector a resource stores and its label                                                                |
| `configuration_schema`, `credential_schema` | JSON Schemas for a resource's `config` and write-only `credential`; `credential_schema` is null without one |
| `authentication`                            | how the type authenticates, as the Harness declares it                                                      |
| `setup_url`, `setup_label`                  | where an operator obtains credentials, when the type names a place                                          |
| `supports_test`                             | whether a resource test makes a non-billable probe ([04](04-resources.md#provider-resources))               |

Each kind adds its own fields:

- **model:** `model_apis`, the calling APIs a model may select, default first; `default_model_api`; `model_api_labels`; `catalog_providers`, the [model catalog](#model-catalog) channels that list the type's own model IDs; and `settings_schemas`, one JSON Schema per calling API for an agent's native model settings. A settings schema is derived from the Pydantic AI settings type the API names: unknown keys are refused, no setting states a default, and members JSON cannot carry are left out. Settings never carry transport (`extra_body`, `extra_headers`, `timeout`, `bedrock_additional_model_requests_fields`), upstream selection (`bedrock_inference_profile`, `openrouter_models`, `openrouter_preset`) provider-account state (`anthropic_container`, `google_cached_content`, `openai_conversation_id`, `openai_previous_response_id`) or server-side tools billed outside `max_usage` (`openai_native_tools`). Agent validation checks settings against the same schema ([04](04-resources.md#validation)).
- **web:** `operations`, the tool operations the type serves (`search`, `scrape`).
- **environment:** `environment_schema`, the template recipe schema; `supports_stop` and `supports_destroy`.

The registry derives each calling API's settings schema when it is assembled, which imports that API's SDK, so the route imports nothing.

## Handles

A handle is opened per use from values resolved in a short database session, after the session closes, and closed when its context exits. The credential is revealed only when the handle opens; no handle, client or credential is cached across uses.

| Kind        | Opened by                                      | Lifetime                                                                   |
| ----------- | ---------------------------------------------- | -------------------------------------------------------------------------- |
| model       | `resources/models/runtime.open_model`          | one attempt's model; reads bounded by `providers.model_timeout`            |
| web         | `resources/web_providers/runtime`              | one attempt's search or scrape backend                                     |
| connector   | `resources/connector_providers/catalog`        | one catalogue read, authorization step or attempt                          |
| Remote MCP  | `resources/connections/oauth.open_mcp_client`  | one discovery, authorization step or attempt                               |
| environment | `runs/environments/adapters`                   | one lifecycle operation, or one attempt's mount ([06](06-environments.md)) |
| memory      | `resources/memories/records.open_record_store` | one API call or purge, or one attempt's record memory ([11](11-memory.md)) |

The web backend's ID is the provider resource ID, so two accounts of one type stay distinct. Fetch and download use the host transport and need no provider resource.

Redis caches under `provider:{type}:` hold only discovery results (MCP tool lists, connector apps and actions) for `providers.discovery_ttl`, keyed by resource version, so any change to the resource, including a new credential, reads fresh. The cache is an accelerator: a miss or a Redis error discovers again.

## Outbound endpoint policy

Requests to models, the model catalog, connectors, record memory backends, Remote MCP servers, OAuth servers, webhook destinations and trace backends go through a host-owned HTTP client (`infra/outbound.py`); web search and scrape backends use the Harness web transport, and environment backends their own clients after the endpoint check below. All of them are under one endpoint policy built from `providers.private_domains`, `providers.private_cidrs`, `providers.http_origins` and `providers.require_https`:

- Cloud metadata addresses are always refused. Loopback, link-local, multicast, unspecified and private addresses are refused unless their domain or network is allowlisted. With `require_https`, plain HTTP is refused except for the allowlisted `http_origins`.
- The policy checks each request URL before it is sent, and each new connection resolves its host once and connects only when every answer is allowed. A DNS answer that changes after validation therefore never reaches a refused address.
- Clients never follow redirects, ignore proxy environment variables, accept only uncompressed responses and read at most `providers.response_bytes` of a body (`payload_too_large` with `limit`). Each call has its own deadline: `providers.operation_seconds` for tests and authorization steps, `providers.tool_call_seconds` for tool calls and discovery, `providers.model_timeout` per model read.

Validation happens where an endpoint is written, where the Service can check it: an MCP connection's URL and a subscription URL are refused as `invalid_argument` on the field. A remote Docker engine an account names and an external target's endpoint are checked whenever the Service dials them (a test, a target's registration or update, or any adapter it builds), and a denial is the environment error `provider_endpoint_denied`. The host is resolved once for the check: a host that does not resolve is the transient `provider_unavailable`, and only a policy refusal is `provider_endpoint_denied`. A `tcp://` engine is checked as `http://`. These backends resolve the host again when they connect, so the single-resolution guarantee above does not cover them.

## Tool sources

A connection becomes one Harness capability per selected connection of a run. Tool sources live in `providers/tools/`; they receive plain values (a URL, a host HTTP client that presents the credential, an entered connector runtime) and never read business tables.

- **Dispatch check.** Tools reach a model and calls leave the worker only through `CheckedToolset`. Before every call, including each retry the model makes, it runs the host's `DispatchCheck` with `{connection_id, provider_id, tool_name, tool_call_id}`; a refusal means the call was never sent. A call without the model's call identity is never sent. A connection exposing more than `MAX_TOOLS` (128) fails the run with `connection_tools_exceeded`.
- **Remote MCP** (`mcp.py`). A run's MCP connection is a Harness `ContextualMCP` whose header factory returns the run's frozen caller headers for that connection ([04](04-resources.md#caller-headers)). The Service never resends a call (`max_retries=0`), a tool error reaches the model as a failed tool result, the server's tool list is cached for the run, and resources, prompts and server instructions are not used. A server listing more than the Harness bound of 2048 tools fails discovery; the connection's tool selection narrows the listing before the 128-tool bound applies.
- **OAuth** (`oauth.py`). The OAuth 2.1 client of Remote MCP servers follows the MCP SDK's discovery rules: protected-resource and authorization-server metadata, dynamic client registration when no client is configured, PKCE S256, issuer checks, the authorization-code, refresh and client-credentials grants, and token revocation. Persisting flows, fencing concurrent use and deciding what an uncertain outcome means belong to the connection service ([04](04-resources.md#connections)).
- **Connectors** (`connectors.py`). The Harness connector definition supplies the provider runtime over the host transport; the Service adapts its catalogue and account operations to tools. A connector action executes at the catalogue version it was listed with, and a call's request ID is derived from the connection, run and tool call, so a resent request can be deduplicated by the provider. An action whose outcome is unknown reaches the model as a failure telling it to check the external state before calling again. A catalogue beyond the Harness discovery bounds fails instead of being truncated.

**Composio.** Composio is a connector provider: a workspace configures it once as a connector provider resource, with the platform's API key as its credential. Each connector connection names one app and its actions and binds one external account through Composio's hosted setup; the connection's credential is the account reference, and Composio keeps and refreshes the app's own tokens ([04](04-resources.md#connections)).

## Model catalog

`GET /api/v1/model-catalog` returns `{items, status}` to any signed-in principal: the models of the public [models.dev](https://models.dev) catalog (`https://models.dev/catalog.json`) that the deployment's model provider types serve. It is the only catalog source; clients seed a new model's `config` and `pricing` from an item and record its `ref` as the model's `catalog_ref` ([04](04-resources.md#models)).

- **Items.** A catalog channel is offered when a registered model type lists it in `catalog_providers`. Of its models, those that output text and were released on or after 2026-04-23 are items, ordered by channel and model ID. An item has `ref` `{provider, model}` (the channel and its model ID), `identity` and `name` (below), the channel's `provider_name`, `release_date`, `characteristics` (the context window, and `image_understanding`, `audio_understanding`, `video_understanding` and `document_understanding` for the input modalities `image`, `audio`, `video` and `pdf`) and `pricing` with `pricing_warning`. A malformed model is left out; a document that is not a catalog, or lists more than 1000 providers or 50 000 models, fails the refresh.
- **Identity.** Copies of one model on several channels share an `identity`, the `lab/model` ID of the catalog's model directory, and take its `name`. A channel's model ID matches a directory ID, its model part or `lab.model` exactly, ignoring case and the separators `.`, `/`, `_` and `-`, after Bedrock region prefixes (`us.`, `eu.`, `au.`, `jp.`, `in.`, `global.`) and Vertex's `@default` suffix are removed. An alias two directory models share matches neither; a model that matches nothing is its own identity, `{channel}/{model ID}`, with its own name.
- **Pricing.** Catalog prices become a pricing entry naming the channel as `provider` and the item's model ID as `model`, which record where the prices came from ([04](04-resources.md#models)), with `source` `models.dev`, `source_revision` the model's `last_updated` date and one always-applying rule `standard`. `input`, `output`, `cache_read`, `cache_write`, `input_audio` and `output_audio` become `input_mtok`, `output_mtok`, `cache_read_mtok`, `cache_write_mtok`, `input_audio_mtok` and `output_audio_mtok`. A context tier becomes a price tier starting above its size and states every price, the ones it leaves unchanged included; `context_over_*` prices pass only beside the tiers they repeat, and a `reasoning` price only when it equals `output`. Prices an entry cannot express exactly (another tier condition or price, a negative or non-numeric price) give `pricing: null` with `pricing_warning` `"Unsupported catalog pricing; configure prices manually"`; no prices give neither.
- **Refresh.** An API-serving process holds the last catalog it read successfully, starting with none, and refreshes it in a task of its lifespan, one refresh at a time. A read that finds the catalog an hour old asks for a refresh and returns the catalog it holds at once; only a read before the first successful refresh waits for one, at most 30 seconds, and a read that gives up never aborts it. A refresh reads at most 8 MiB within 30 seconds, instead of `providers.response_bytes` and the per-call deadlines, under the [outbound endpoint policy](#outbound-endpoint-policy). A failed refresh, whatever its error, is logged with its error type, keeps the last catalog and is retried for a read at least 60 seconds later.
- **Status.** `ready` after a successful refresh; `stale` when the last refresh failed and an earlier catalog is served; `unavailable` with no items before any refresh succeeds. There is no bundled catalog and no setting to disable the fetch.

## MCP server catalogue

`GET /api/v1/mcp-servers` suggests Remote MCP servers for new connections to any signed-in principal, filtered by `query` (at most 128 characters, matched against key, name and description) and paged in key order ([10](10-api.md#collections)). The list is the packaged `providers/tools/mcp_servers.json` plus the deployment's `providers.mcp_servers`, where a deployment entry replaces the packaged entry with its key.

An entry is `{key, name, description, url, auth, header_names, documentation_url, logo_url, requirements}`; `auth` is one of the modes a connection supports, and `header_names` lists the credential headers exactly for `headers` authentication. An entry only prefills a connection: creating the connection still validates the endpoint and authentication.

## Environment providers

Environment definitions are reached only through the registry; business packages never import `providers.environments` ([02](02-layout.md#import-direction)). The registry also owns the check of the endpoint an environment account names (`check_environment_endpoint`: a Docker engine other than the operator's, which `providers/endpoints.py` extracts). External envd targets are no registered type: `providers/envd.py` normalizes their endpoint and token, checks the endpoint with the same policy, reads the device a daemon states, and builds the Harness `http_envd` adapter from a target's values and portable state ([06](06-environments.md#external-targets)). The environment lifecycle, the operations a definition performs and the test probe are [06](06-environments.md)'s and [04](04-resources.md#provider-resources)'s.

## Trace backends

The trace backend is operator configuration, not a provider resource or registry kind. The `telemetry` settings select one backend (`trace_backend`: `none`, `langfuse` or `logfire`) with its `trace_url` and keys, and that one backend is both the export target of Harness spans ([12](12-observability.md#traces)) and the source of trace queries ([07](07-facts-and-delivery.md#trace-query)).

```python
@dataclass(frozen=True)
class SpanQuery:
    attributes: Mapping[str, str]      # every attribute must match exactly
    started_after: datetime
    started_before: datetime
    limit: int
    trace_id: str | None = None
    roots: bool = False                # only trace roots: one per attempt
    cursor: str | None = None

class TraceProvider(Protocol):
    type: ClassVar[Literal["langfuse", "logfire"]]
    @property
    def otlp_endpoint(self) -> str: ...
    @property
    def otlp_headers(self) -> dict[str, str]: ...
    async def query(self, query: SpanQuery) -> SpanPage: ...
```

- A backend reports spans as it stored them. Nothing it returns is trusted for tenancy: callers check every span's correlation attributes themselves.
- Every read is bounded by `telemetry.trace_query_timeout` and 8 MiB of response. Any backend failure is `unavailable` with dependency `trace:{type}`.
- The operator configured the backend, so its own host may be private; cloud metadata addresses stay refused.
- Fields a backend does not keep are null or empty. Langfuse keeps no span events or links and gives each span a `source_url` to the trace in its UI; Logfire gives none.

## Installed Harness plugins

`plugins.keys` installs Harness plugin factories by entry-point key; nothing else is imported, and a key without an installed factory fails startup. Agents select instances of installed plugins by `plugin_key` with their own configuration, and revision validation asks the factory to accept that configuration ([04](04-resources.md#validation)). There is no route listing installed plugins.

## What a provider may and may not do

- It receives plain values: the resource's `config`, the revealed credential, extra headers, a host HTTP client or transport under the endpoint policy, and for environments the recipe and portable state. It never receives a database session, a row or a principal.
- It performs I/O only within its caller's deadline and response bound, and reports classified errors. The Service records only a code or fixed text, never an upstream body, and treats an error it cannot classify as an unknown outcome, never as safe to repeat.
- It may use Redis only for the discovery caches above. It creates no tables in the core schema; a distribution's provider that needs durable rows contributes its own tables and migrations ([09](09-runtime.md#extension-points)), and no core table references them.
- Adding a backend type is adding its definition to the distribution's `providers`; no core schema or CHECK constraint changes, because rows store the `type` string.
