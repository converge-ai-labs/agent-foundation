# Search Provider Management

## Design Position

Service supplies first-party web search through a configured Search Provider and an explicit `AgentConfig.search` selection. Users configure an account once and reuse it across eligible Agents. Selecting an account composes the Harness search capability and its authorized runtime bindings without a user-created Connector or installed-plugin selection.

A Search Provider is an Organization- or Workspace-owned account resource, not a Model Provider, executable plugin, generic credential container, or separately deployed service. Its credentials and administrative availability change independently from Agent Revisions. Agent configuration retains a resource reference and bounded search behavior; it never contains credentials, endpoints, clients, or provider code.

## Boundaries

| Concern                                                           | Owner                                                                                               |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Search Provider types, account resources, selection, and live use | This document                                                                                       |
| Agent Revisions, effective configuration, and typed Run overrides | [Agent Management](28-agent-management.md)                                                          |
| Scope, product permissions, and security audit                    | [IAM](33-identity-and-access-management.md)                                                         |
| Resource-owned encryption and credential generations              | [Secret Management](27-secret-management.md#protection-boundary)                                    |
| Search tools, provider-neutral results, and feature exposure      | [Harness Web resources](../a13n-harness/09-context-and-memory.md#media-documents-and-web-resources) |
| Attempt lifetime and fresh root/child bindings                    | [Service–Harness integration](14-harness-runtime-integration.md)                                    |
| Search setup and account selection in the browser                 | [Console](../frontend/console.md#search-setup)                                                      |

Connectors and remote MCP connections remain the extension path for other search integrations under [External Connectivity](40-connectivity/README.md). They retain their own schemas, credentials, and tool identities. A Search Provider does not expose arbitrary external tools or change those resources.

## Trusted Provider Types

The Service distribution owns a finite, code-reviewed Search Provider type catalog. Each definition exposes the following conceptual metadata:

```python
class SearchProviderDefinition:
    type: str
    display_name: str
    configuration_schema: JsonObject
    credential_schema: JsonObject
    credential_required: bool
    setup_url: str
```

`type` selects a trusted implementation family and is a 1–64-character lowercase ASCII identifier beginning with a letter and containing letters, digits, or underscores. `display_name` contains 1–128 Unicode scalar values. The implementation's typed models own configuration parsing and the safe JSON Schema Draft 2020-12 metadata used by clients. Credential input has a separate schema marked `writeOnly` and never appears in ordinary configuration or schema defaults. `setup_url` is a distribution-owned HTTPS account/setup link bounded to 2,048 characters, not a user-selected request destination. Schemas are self-contained, bounded to 64 KiB each, and contain no remote references or executable transformations. The catalog contains at most 100 definitions with distinct type keys, ordered by `type`.

The initial first-party catalog is:

| Type    | Account prerequisite                          | Non-secret configuration | Search source                                                                               |
| ------- | --------------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------- |
| `brave` | A Brave Search API key with Web Search access | Empty object             | [Brave Web Search API](https://api-dashboard.search.brave.com/api-reference/web/search/get) |
| `exa`   | An Exa API key with Search access             | Empty object             | [Exa Search API](https://exa.ai/docs/reference/search)                                      |

Both types require a write-only credential string. They use fixed official HTTPS API destinations and reject caller-supplied endpoints, headers, proxies, arbitrary upstream parameters, and credential-source references. Search adapters return search results rather than invoking upstream answer generation, deep-research jobs, or a second Model. Account plans, geography, quotas, and upstream availability remain provider-owned; catalog membership and a configured key do not promise free or successful service. Provider adapters enforce documented upstream input limits and map violations to safe errors instead of silently truncating a query.

Additional provider types require a distribution addition with configuration, credential, transport, error, and result-contract validation. A keyless type has `credential_required=false` and rejects credential input; it still requires an explicitly created and selected Search Provider. Process startup and an empty Workspace create no search accounts or default selection. The initial catalog has no keyless provider or automatic provider fallback.

Control owns catalog reads, account management, and explicit tests. Worker owns search execution. Both consume the distribution's first-party definitions; Connectivity has no search execution role. This catalog is independent from the Worker-installed plugin factory catalog and is not a second public Capability-enable registry. The same release supplies compatible definitions and adapters; missing execution support fails preparation and never installs code at runtime.

## Search Provider Resource

The conceptual resource is:

```python
class SearchProvider:
    id: SearchProviderId
    organization_id: OrganizationId
    workspace_id: WorkspaceId | None
    type: str
    name: str
    configuration: JsonObject
    credential_configured: bool
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`SearchProviderId` allocates the `sprov` object-ID prefix. Identity, Organization, Workspace, and type are immutable. Ownership and automatic parent visibility follow [Organization-owned configuration](33-identity-and-access-management.md#organization-owned-configuration). An Agent can select a local or parent-Organization Provider, never a sibling-Workspace or cross-Organization Provider. Names are trimmed, contain 1–128 Unicode scalar values, and are case-insensitively unique within the owning scope. Several Providers can use the same type; equal names in different visible scopes are disambiguated by owner and ID, with no name-based precedence.

Create accepts `type`, `name`, `configuration` (default `{}`, at most 16 KiB of JSON), `enabled` (default `true`), and a separate write-only `credential`. For the initial types, the credential is a nonblank string bounded to 4,096 UTF-8 bytes, stored without normalization; a required credential is validated even when creating a disabled resource. Reads expose only `credential_configured`, never a masked value, suffix, ciphertext, generation, or reusable Secret ID. There is no separately addressable Credential or managed Secret for this account.

PATCH can change name, configuration, enabled state, and credential. A supplied configuration replaces the entire typed object; omission retains it. Omitting credential retains the current value, while supplying a valid value atomically replaces it. Explicit null removes a credential only for a type permitting an unauthenticated configuration; the initial types reject removal. Disabling an account retains its encrypted material and references. A credential rotation neither modifies Agent Revisions nor proves that the old key was revoked upstream.

Every mutation uses the shared resource-owned protection primitive, short transaction, and atomic security audit. Name, configuration, enabled state, or credential replacement changes the strong ETag. Credential replacement advances its internal credential generation even when the submitted value is unchanged. A non-credential semantic no-op leaves the representation tag unchanged. Search Providers have no public version, Revision history, archive, or individual delete operation; disabling is the supported withdrawal of use. Owning-scope deletion clears credentials through its authorized resource cleanup while retained execution and audit references keep their historical meaning.

## Agent Selection

`AgentConfig.search` is absent or null for no first-party search, or contains one complete selection:

```python
class SearchSelection:
    provider_id: SearchProviderId
    max_results: int = 5
    include_domains: tuple[str, ...] = ()
    allow_domains: tuple[str, ...] = ()
    deny_domains: tuple[str, ...] = ()
```

`max_results` is between 1 and 10 and is both the default and ceiling for one tool call. `include_domains` contains at most 20 distinct DNS hostnames, normalized to lowercase ASCII IDNA without a trailing dot, each at most 253 bytes. URLs, ports, IP literals, paths, and wildcard syntax are invalid. An empty tuple imposes no domain filter. A nonempty tuple admits only results whose URL hostname equals a listed domain or its subdomain at a DNS-label boundary. The adapter enforces this after parsing results, whether or not it also passes a provider-native domain hint. It preserves provider order and may return fewer results, including zero; it does not issue extra requests to fill the count. This is a result filter, not an outbound-network authorization grant or a guarantee of exhaustive coverage.

`allow_domains` and `deny_domains` use the shared [Harness domain restrictions](../a13n-harness/09-context-and-memory.md#media-documents-and-web-resources): exact hosts or explicit `*.` subdomains, deny precedence, and at most 256 entries per list. Service applies them to returned search results and to first-party Web fetch/download/scrape destinations, checking every redirect before DNS or network I/O while retaining address pinning and current transport authorization. The existing `include_domains` filter remains independent and all selected filters apply; it does not gain wildcard syntax or change into an outbound grant. Empty new fields are omitted from durable JSON so existing selection digests remain valid. Neither setting restricts arbitrary shell, plugin, or remote MCP egress.

The Agent authoring operation validates the complete selection, current ownership, enabled state, required credential presence, and supported Provider type without decrypting or making an external request. It stores only the stable reference and normalized parameters. Changing the reference or parameters publishes an Agent Revision. Creating, renaming, disabling, or rotating a Provider never rewrites a referencing Revision.

The full `AgentRunOverride` uses the same `search` type: omission inherits, null disables, and an object replaces the whole selection with its documented defaults. The field carries no inline account or secret. Narrow overrides on ingress or other domain-specific surfaces remain limited to their own allowed fields; this contract does not expand them implicitly. Search is not a `RunCapabilityOverlay` entry and is unaffected by that overlay's include/exclude or inheritance flags.

Run acceptance checks the selected Provider for every node in the accepted graph and freezes each node's `SearchSelection` in its `EffectiveAgentConfig` and digest. No Provider configuration or credential material is copied into this snapshot. Same-Run recovery and explicit Retry retain the accepted selection; ordinary new invocation and successor selection follow Agent Management and control-contract inheritance/override rules. Live Provider changes are handled at the outbound boundary below, not by selecting a different account.

## Execution and Tool Contract

Before Harness entry, Worker rechecks the selected graph's account scope, enabled state, required credential presence, and local adapter availability without making a search request. For each selected node, Service composes one `WebCapability` with explicit configuration: search mode `host`, one exact backend for the selected Provider, the selected result ceiling, scrape mode `off`, and the standard fetch/download tools. A fresh `WebRunCapability` supplies the policy, transport, and authorized search adapter for that logical Harness Run. Service supplies this built-in composition directly; users do not also select a search plugin. A conflicting plugin contribution of the same Capability identity fails the normal composition checks rather than overriding the selection.

`search`, `fetch`, and `download` (stable tool IDs `web.search`, `web.fetch`, and `web.download`) are exposed together using the standard Harness Web Toolset. A Run selecting this capability requires an Environment; Worker rejects a missing root Environment with `environment_required` before Harness entry, and each root or child Web invocation enforces the same prerequisite. Environment selection and preparation follow the existing Thread/Run and child Environment contracts. Search result disclosure uses the existing bounded Harness projection and can truncate when no authorized output file is available. Downloads write only through the bound Environment and its current file permissions. Scrape, native Model search, and provider-native answer generation are not implicitly enabled. An absent selection contributes neither this Capability nor its clients and makes no search requests. Explicit Model-native or custom tools retain their separate contracts.

The model-visible function reuses the Harness contract:

```python
search(query: str, num: int | None = None)
```

`query` is nonblank and bounded by the Harness query contract; `num` follows its 1–100 input bound and is clamped to the Agent's `max_results`. The model cannot choose an account, credential, endpoint, or provider type. The Host adapter maps the request to one selected search service and normalizes each result to `title`, `url`, and `snippet`. Missing title or snippet is an empty string. URLs retain citation provenance under the Harness URL-redaction rules, and snippets contain bounded provider-supplied text rather than an extra summarization call. Results are untrusted context and never become instructions or authorization.

Success follows the Harness `{ok: true, results: [...], showing: n}` result and its ordinary truncation/disclosure fields. Empty valid results are successful. Failures use `{ok: false, error: {code, retry_hint}}` through the same tool boundary; an upstream error is never disguised as an empty result. The [Harness Web contract](../a13n-harness/09-context-and-memory.md#media-documents-and-web-resources) owns the provider protocol, result bounds, and tool projection rather than a parallel Service tool schema.

### Live authorization and credentials

Every outbound search dispatch, including a permitted retry, rechecks the current Attempt lease/fence, root and executing Agent invocation permissions from the [Attempt IAM snapshot](33-identity-and-access-management.md#attempt-iam-snapshot), selected Provider scope, and enabled state. The selection permits use only within the accepted Agent graph and current Harness `tool.call` authority. Direct Agent invocation permission does not confer account management or a general-purpose Search API. Inline children use their own frozen selections and fresh logical-run bindings within the owning Attempt's IAM snapshot; async children retain the accepted subtree and resolve resources under their own Attempt. A parent's selection or live client is never inherited merely because it spawned a child.

The adapter captures current Provider configuration and encrypted credential material in a short authorized database read, closes the session, and then decrypts and constructs the request-owned client. No credentials are placed in process environment variables, Agent revisions, Run payloads, plugins, model context, or portable Harness state. Account credentials are attached only to that provider's approved HTTPS destination; redirects are rejected, and deployment egress policy can further restrict access. Search-result URLs are not fetched by the search operation. Explicit `fetch` and `download` calls use a separate credential-free Web client, recheck current attempt and Agent authority, validate each HTTP(S) destination against Service endpoint policy, and connect to an authorized resolved address while preserving the original Host and TLS server name. Redirects receive the same checks and never carry cookies or Search Provider credentials across hops. Response headers, bytes, redirects, and deadlines remain bounded by the Harness Web configuration; downloads retain Environment authorization and cleanup.

Search, fetch, and download check the latest published Attempt IAM snapshot at dispatch, retry, redirect, and response-delivery authorization boundaries. After the response, current Attempt and Provider eligibility are checked again before disclosing content. Provider disablement or upstream credential revocation prevents later dispatch and disclosure after the check observes it; it cannot retract an already sent query or its upstream charge. Rotation applies to the next credential acquisition. An in-flight request can finish with the credential it acquired, subject to the post-response eligibility check. Clients and sensitive references are released on success, error, cancellation, and Attempt exit; a replacement Attempt never restores an old client or key from state.

### Deadlines, failures, and usage

One search call has a 30,000 ms total deadline across authorization, transport, any retry wait, and result processing, further bounded by invocation cancellation and deployment policy. The transport bounds response bytes to 1 MiB and uses the Harness header and normalized-result limits. Provider SDK automatic retries are disabled. Service permits at most two total dispatches, to the same account, only for an explicit upstream rate-limit or temporary-unavailable response without a result. A retry requires remaining budget and current authorization; it honors `Retry-After`, or waits 1,000 ms when absent. A requested wait beyond the remaining deadline returns the original failure without retrying early. It never switches account, provider type, or native search mode.

| Condition                                                                                                    | Observable outcome                                                                                                         | Automatic retry                  |
| ------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| Missing/concealed Provider, wrong scope, disabled Provider, or missing required credential at save/admission | `search_provider_not_found`, `search_provider_disabled`, or `search_provider_credential_missing`; no Agent/Run commit      | No                               |
| Unsupported first-party adapter at Worker preparation                                                        | `search_provider_unavailable`; Harness does not start                                                                      | No code installation or fallback |
| Lost execution authority, revoked Provider eligibility, or missing required credential during execution      | Fatal authorized-resource failure through the existing Run error boundary; no further search dispatch or result disclosure | No                               |
| Provider rejects authentication                                                                              | `web_search_authentication_failed`                                                                                         | No                               |
| Explicit exhausted balance or quota                                                                          | `web_search_quota_exceeded`                                                                                                | No                               |
| Upstream rate limit                                                                                          | `web_search_rate_limited`                                                                                                  | Only the bounded retry above     |
| Explicit temporary upstream unavailability                                                                   | `web_search_unavailable`                                                                                                   | Only the bounded retry above     |
| Invalid/unsupported query                                                                                    | `web_search_request_invalid`                                                                                               | No; model can change the query   |
| Deadline expires                                                                                             | `web_timeout`                                                                                                              | No automatic replay              |
| Invalid or oversized response                                                                                | `web_search_response_invalid`                                                                                              | No                               |
| Other upstream/transport failure                                                                             | `web_search_failed`                                                                                                        | No                               |
| Cancellation                                                                                                 | Propagated cancellation and bounded cleanup                                                                                | No                               |

Provider adapters classify only documented status/error evidence. They do not label every HTTP 429 as exhausted quota or guess a balance from a failed request. Public codes and retry hints contain no raw response, endpoint, authorization header, or exception text. A model-visible dependency failure can be handled by the Agent; loss of execution authority cannot be converted into a recoverable search result.

Search is read-only but can consume quota on every dispatch. Timeout, cancellation, or a crash after dispatch leaves upstream execution/charging uncertain. Recovery may repeat an uncheckpointed search under the existing tool recovery contract; no exactly-once execution, search-result cache, or billing deduplication is implied. Provider-reported usage follows `ProviderUsage` and `AgentContext.record_provider_usage()` with source/tool ID `web.search`. Unknown usage or cost remains unknown; Service never estimates a zero charge from missing receipts. Tests outside an Agent Run do not invent a Run or Run UsageRecord.

Authorized Run configuration and safe invocation observations identify the selected Provider ID and type, tool-call correlation, outcome code, duration, and available usage. Provider trace attributes and search content follow the existing [observability registry and content policy](38-observability.md); ordinary logs and audit contain no queries, returned content, credential values, or raw provider errors. Security audit and traces do not become account or execution authority.

## Management API

All routes use `/api/v1`, the shared error envelope, and [API conventions](../api-conventions.md). The account and catalog surfaces are served by Control and `all` roles.

| Method and route                                                        | Result                                                |
| ----------------------------------------------------------------------- | ----------------------------------------------------- |
| `GET /search-provider-types`                                            | Complete bounded `{items: [...]}` type catalog        |
| `GET /search-provider-types/{type}`                                     | One safe definition                                   |
| `POST /workspaces/{workspace}/search-providers`                         | Create a local account; `201` with resource and ETag  |
| `GET /workspaces/{workspace}/search-providers`                          | Visible local and Organization accounts               |
| `GET /workspaces/{workspace}/search-providers/{provider_id}`            | Safe visible resource and ETag                        |
| `PATCH /workspaces/{workspace}/search-providers/{provider_id}`          | Update a local account under `If-Match`               |
| `POST /workspaces/{workspace}/search-providers/{provider_id}/test`      | Test a saved visible account                          |
| `GET /workspaces/{workspace}/search-providers/{provider_id}/references` | Referencing Agent Revisions visible in this Workspace |

Organization-owned management uses equivalent `/organizations/{organization}/search-providers` routes. Organization collections contain only Organization-owned accounts. Reads and tests through a Workspace can address a visible parent account, but mutations must address its owning scope. Type catalog reads require an authorized Organization or Workspace context and reveal no account configuration.

Account lists use standard cursor pagination ordered by `(casefold(name), id)` with optional exact `type` and `enabled` filters; disabled accounts remain visible. Reference lists include retained current and historical Agent Revisions, ordered by `(agent_id, version, agent_revision_id)`, with `agent_id`, `agent_revision_id`, `version`, and `is_current`. They apply both account-read and Agent-read authorization; Organization queries span only authorized descendant Workspaces. Results exclude inaccessible Agents rather than exposing their names or counts. A reference is inspection evidence, not permission to run or mutate that Agent.

Create is synchronous and retains no separate idempotency record, following the existing Provider-management convention. A duplicate normalized name returns `409 search_provider_name_conflict`. After a lost create response, clients reconcile through the account collection before creating again; they cannot compare the stored credential. PATCH requires a strong `If-Match`; missing and stale preconditions return `428` and `412`. Unknown types/fields, invalid configuration, and invalid credential input return `400`; absent or concealed resources return `404`. Mutations return only safe resources and never echo secret input in errors.

### Account test

`test` accepts an empty object and checks a saved account through the same adapter and live credential/egress boundary. It requires no Agent, Model, Environment, or Connector. For the initial providers it performs one fixed, non-sensitive search for `Agent Foundation`, requesting one result without Agent domain filters. It makes at most one dispatch; even zero results prove a successful parsed request. The operation can consume upstream quota, and UI/SDK callers must not automatically repeat it after an uncertain outcome.

An authorized completed test returns `200` with `{success: bool, code: str | null, checked_at: datetime}`. Success has null code; dependency failure uses the safe search code above. Resource, authentication, and authorization failures use ordinary HTTP errors. The operation retains no search response or test resource, makes no Agent mutation, and does not set a persistent connected/verified flag. It rechecks account eligibility and the selected representation before reporting; concurrent account changes return `409 search_provider_changed` rather than certifying a different configuration. A successful test proves only that request at that time. Saving an account, testing it, and saving an Agent remain independent completion boundaries.

## Authorization and Audit

The IAM registry owns two product actions: `search_provider.read` for safe definitions, accounts, and references, and `search_provider.manage` for local account creation, update, credential replacement, enable/disable, and testing. Workspace Viewer receives read; Builder and Admin receive management for local accounts and testing of visible parent accounts. Only Organization Admin through the Organization management boundary mutates Organization accounts. Account naming or creation grants no extra privileges.

Binding a Provider while authoring an Agent follows that Agent's authoring authorization plus current Provider scope and eligibility checks. Invoking an already configured Agent follows current `agent.invoke` authority plus the selected-resource and Harness tool checks above. Agent-scoped grants permit use only through that Agent's accepted selection and grant neither account reads nor account management. Explicit Run overrides that introduce or change a Provider require `search_provider.read` in the consuming scope as well as the owning Run operation; inherited selections do not acquire a new management permission requirement. Provider credentials never become a plaintext-read permission.

Create, update, credential replacement, enable/disable, and test produce security audit events under the shared atomicity rules. Detail fields are limited to related IDs, changed field names, and safe outcome codes. Test records use a separate short audit transaction after external I/O; no audit transaction spans the probe. Reference reads never disclose credential material.

## Compatibility and Invariants

Missing `search` in pre-existing Agent and effective configurations means no first-party search. Readers preserve that meaning without rewriting immutable historical payloads or their digests. New selections require compatible Control, Worker, Harness, and SDK schemas before use; an older consumer must fail explicitly rather than drop the selection. Search account configuration and credentials are live operational dependencies, not reproducible historical revisions. Compatible adapter updates preserve frozen selection semantics, tool identity, domain filtering, and safe result/error contracts.

1. One selected Search Provider yields one explicit Host search backend; it never silently selects native Model search or another account.
2. Agent Revisions and accepted Runs contain only references and bounded parameters; credential rotation leaves them unchanged.
3. Every credential acquisition and result disclosure applies current execution authority and resource eligibility with no database session held across external I/O.
4. Root, inline-child, async-child, retry, continuation, and recovery paths honor each executing node's own accepted selection and fresh runtime lifetime.
5. No selection creates no search capability or outbound request; a selected but unusable account fails preparation or the live dispatch check explicitly.
6. Selecting search exposes the standard search/fetch/download combination, requires a Run Environment, and preserves Environment file permissions for downloads; scrape and native search remain disabled.
7. Account creation, account testing, Agent revision publication, upstream dispatch, Run completion, and usage observation remain distinct outcomes.
8. Provider failures and unknown charging outcomes remain observable without credentials, raw errors, or query content in ordinary diagnostics.
9. The API and all Service SDKs preserve omission, null, full replacement, ETags, and partial setup outcomes without a second credential or plugin-management workflow.
