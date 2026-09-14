# Connector Providers, Connectors, and Connections

## Design Position

Connector Providers provide general outbound SaaS capabilities. Service can configure several accounts or endpoints of the same Provider type, discover the Connectors each one offers, and establish independently authorized Connections. Composio and other registered adapters remain optional Connectivity components rather than Service core dependencies.

The external integration service owns third-party account authorization, OAuth callback processing, access and refresh tokens, token rotation, and provider API invocation. Service owns its configured Connector Provider, safe discovered Connector values, Connection projection, assignment and authorization, exact Run selection, and Agent-facing a13n MCP boundary.

## Connector Provider definitions

The distribution registers trusted Connector Provider implementations explicitly. Installation alone grants no trust. Each implementation supplies one safe definition:

```python
class ConnectorProviderDefinition:
    type: str
    display_name: str
    configuration_schema: JsonObject
    credential_schema: JsonObject
```

The implementation's strongly typed configuration model is the single authority for both `configuration_schema` and deterministic validation. The schema contains non-secret fields, bounds, defaults, and descriptions. `credential_schema` describes the write-only service-access credential input accepted by the owning create or credential-rotation operation. Service stores those values as an encrypted credential bundle on the ConnectorProvider rather than embedding them in configuration or returning them on reads. Connection testing and Connector discovery perform external I/O only after configuration has passed deterministic validation. Type-definition reads inspect safe registered metadata only, never accounts or upstream catalogs.

There is no cross-domain Provider definition or runtime base class. Connector Provider definitions, Model Provider definitions, and Environment Provider specifications have distinct operations and lifecycles even when management surfaces render their schemas similarly.

## Connector Provider

The following schemas are conceptual and are not wire or ORM models:

```python
class ConnectorProvider:
    id: ConnectorProviderId
    organization_id: OrganizationId
    workspace_id: WorkspaceId | None
    name: str
    type: str
    configuration: JsonObject
    credential_generation: int
    status: Literal["active", "disabled"]
    version: int
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`type` selects one deployment-registered Provider implementation and is not the Provider's identity. A Workspace can configure several Connector Providers of the same type, such as company and personal Composio accounts; they have different IDs, configuration, credentials, and lifecycle. `configuration` is validated by the selected implementation's strongly typed model rather than interpreted as an arbitrary JSON dictionary. Endpoint, deployment mode, and every other non-secret implementation-specific setting live inside that one configuration value rather than in competing top-level fields.

Ownership, type, and behavior-defining configuration are immutable. A null `workspace_id` denotes Organization ownership under [Organization-owned configuration](../33-identity-and-access-management.md#organization-owned-configuration). Changing one creates another Connector Provider so an accepted Run cannot silently dispatch to a different backend under the same ID. Name, credential rotation, safe observations, and administrative status can change under exact management-version preconditions without changing Connector Provider identity.

`credential_generation` identifies the current Provider-owned encrypted bundle that authenticates Service to the external integration service. The bundle can hold a self-hosted access token or BYOK integration-service API key, protected using the [shared credential protection contract](../27-secret-management.md#protection-boundary). Provider authoring supplies write-only values; management reads return safe metadata and never material or a Secret reference. The owning record stores ciphertext, nonce, and encryption-key identifier. Replacement atomically advances the generation and resource version. The bundle never holds a third-party account OAuth token.

`active` means the Connector Provider is administratively enabled; it is not a continuous health claim. Disabling it prevents new setup, discovery, and dispatch without reinterpreting or deleting retained Connector Connections, Run selections, or audit facts. Transient endpoint or credential failures remain bounded safe observations rather than another lifecycle state.

## Connector discovery

A Connector is an advisory Provider-scoped directory value describing one integration offered by a configured Connector Provider:

```python
class Connector:
    connector_provider_id: ConnectorProviderId
    key: str
    name: str
    description: str | None
    logo_url: str | None
    unavailable_reason: str | None
    setup_schema: JsonObject
    authentication_methods: tuple[str, ...]
```

`discover_connectors` executes against one exact enabled Connector Provider using its current configuration and credential. Results can differ between two Providers of the same type. They are bounded safe values used to choose and prefill setup; they are not Service resources, authorization grants, tool catalogs, or proof that setup will succeed. A Connector key is scoped to its Provider and is not assumed equivalent to the same key returned by another Provider.

Installed implementation discovery, Connector discovery, [tool preview before account authorization](08-built-in-connector-adapters.md#tool-discovery-contract), existing Connector Connection reads, and per-Connection execution discovery are separate operations. An implementation without an upstream enumeration API can return a bounded implementation-owned catalog through `discover_connectors`; it never turns arbitrary caller input into a trusted implementation or Connector.

The selected implementation validates and safely projects upstream catalog metadata. `setup_schema` describes only non-secret setup options, such as a supported authentication configuration selector. It never solicits third-party passwords, API keys, cookies, or OAuth tokens. Authentication-method keys retain Provider-specific semantics, and a method is advertised as usable only when the external service offers the required hosted authorization or credential form. Generic JSON Schema form rendering does not replace the authorization ceremony.

Discovery uses bounded pagination, entry counts, schema size, and total bytes under the [discovery safety bounds](04-agent-facing-tools.md#discovery-and-result-bounds). Service persists one complete directory snapshot per exact Provider and credential generation. Search and cursor pagination read that snapshot; cursors bind the Provider, normalized search and snapshot time. An explicit refresh publishes only after every upstream page and metadata check succeeds. Failure leaves the prior snapshot available for ordinary reads. Credential replacement invalidates the snapshot atomically, and a refresh started under an older credential cannot republish it. Snapshot publication does not change the Provider's administrative version. Directory entries have no standalone CRUD resource or enable/disable list, and refreshing them never creates Connections. Tool schemas are not part of this cache. Setup revalidates current Provider eligibility, selected Connector, and setup options. A discovery failure reports a bounded error without modifying saved connections or treating an incomplete result as a complete catalog.

Organization ConnectorProviders are automatically usable from all descendant Workspaces. Each Connection still belongs to its consuming Workspace and references a Provider in that Workspace or its parent Organization. External authorization correlation binds the consuming Workspace and exact Provider; using a shared Provider never merges external accounts across Workspaces.

## Connection

A Connection is the stable Workspace resource for one configured external tool source. It owns a discriminated `source`, current authorization, lifecycle, and safe observations. A Connector source fixes a `provider_id` and `connector_key`; an MCP source fixes an endpoint, authentication mode, and static header names. [Remote MCP](06-remote-mcp-connections.md) owns its protocol-specific behavior. Source identity is immutable. Display name, authorization, and administrative state can change while the Connection ID remains stable.

The following schema is conceptual, not a wire or ORM model:

```python
class Connection:
    id: ConnectionId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    name: str
    source: ConnectorSource | MCPSource
    status: Literal["pending", "ready", "action_required", "disabled"]
    status_reason: Literal["reauthorization_required", "incompatible"] | None
    version: int
    authorization_generation: int
    credential_configured: bool
    safe_metadata: JsonObject
    last_check: ConnectionCheck | None
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime

class ConnectorSource:
    kind: Literal["connector"]
    provider_id: ConnectorProviderId
    connector_key: str

class MCPSource:
    kind: Literal["mcp"]
    endpoint_url: str
    auth_mode: Literal["none", "bearer", "oauth", "static_headers"]
    static_header_names: tuple[str, ...]
```

Creation validates the immutable source and stores a pending Connection. It never initiates account authorization, invokes a tool, or claims that a remote account is usable. An MCP endpoint without authentication becomes usable through an explicit check. `ready` means that the latest authorization or check established eligibility; it is not a continuous health guarantee. `action_required` carries a status reason. `disabled` denies discovery, new authorization, Run acceptance, and dispatch. Enabling permits explicit verification again; it does not establish fresh remote authorization.

`version` is the compare-and-swap version for management changes. `authorization_generation` is a separate compatibility boundary for accepted Runs. Each successful authorization advances it, including reauthorization to the same upstream account. Normal OAuth token refresh advances the encrypted credential generation without advancing the authorization generation. Neither display-name changes nor ordinary credential refresh invalidate accepted Run selections.

A new authorization can bind a different upstream account under the same Connection ID. Service verifies the returned account against that exact authorization attempt before replacing the binding. An opaque external account reference and provider correlation remain private. They are never ordinary model arguments or caller-selected execution context.

## Application authority and ownership

A Connection belongs to a Workspace, not to an application end user. An upper application owns its own User-to-Connection mapping and signs management requests with a Service Account API key authorized for that Workspace. Service neither requires an a13n Console user for authorization nor creates an a13n user to represent the application's customer. The initiating principal remains the authority for completion and protected authorization reads.

`connection.read` grants safe Connection reads and selection; `connection.manage` grants lifecycle and authorization management. Builder and Administrator roles can manage Connections, subject to credential boundaries and current Workspace eligibility. Provider configuration remains under its separate administrative authority. Agent and AccountTarget configuration reference Connection IDs; Connections do not carry a competing mutable assignment list.

## Credential custody and setup

Externally managed Connector credentials remain with the external integration service. Direct API-key, bearer, or basic-auth setup accepts separate write-only credential fields, delivers them only to the chosen adapter, and never places plaintext material in configuration, command receipts, logs, or Connection reads. The [built-in adapter contract](08-built-in-connector-adapters.md) defines supported credential schemes. Remote MCP credentials are encrypted on the Connection under the [shared credential protection contract](../27-secret-management.md#protection-boundary).

Connection creation, authorization, checking, and tool invocation are independent operations. Tool invocation occurs only inside an authorized Agent Run. The Connection API supplies no standalone tool-execution endpoint.

## Authorization

`POST /connections/{id}/authorizations` creates one queryable short-lived authorization operation under an idempotency key and exact Connection version. Its method is `browser`, `credentials`, or `client_credentials`, subject to source support. Direct credentials and machine OAuth require no browser. Authorization replay returns the existing operation and does not repeat an upstream side effect.

The public status is `preparing`, `awaiting_user`, `awaiting_completion`, `processing`, `completed`, `failed`, `expired`, or `cancelled`. `next_action` can request opening a URL, checking the Connection, or restarting authorization. Completion means the authorization operation reached its confirmed outcome; it does not imply current Connection readiness. For example, a committed credential can have a completed authorization and a `check_connection` action when bounded MCP discovery could not establish readiness. Safe errors distinguish known failure from `outcome_unknown` after a potentially effective upstream request. An unknown outcome does not advertise a restart action; inspect the retained operation before deciding whether to authorize again.

A newer attempt supersedes older attempts through the Connection's authorization-attempt generation. Completion validates operation ownership, current authority, generation, Connection version, and protocol evidence before changing the binding. Late replies and background reconciliation never attach a superseded account. Cancellation and expiry erase usable handoff material and prevent local completion; they do not claim to undo an already effective upstream action. Unknown operations are not blindly retried.

### Browser handoff

The application backend creates browser authorization with its own unpredictable `state`, an exact registered `return_url`, and the SHA-256 challenge of an unpredictable completion verifier. The return URL uses HTTPS, except that local development can use plain HTTP only with the exact host `localhost`, `127.0.0.1`, or `[::1]`; alternate spellings, subdomains, and other loopback addresses are rejected. The configured Service `public_origin` follows the same transport rule. The backend retains the verifier. The returned launch URL is a narrowly scoped bearer capability, not a general Service API credential.

Service hosts the minimal browser handoff. The browser binds the launch capability to a per-tab nonce and follows the provider URL. OAuth or hosted-form returns pass through the same browser binding. The application receives its original state, authorization ID, and a short-lived receipt at its registered return URL. It validates its state and completes authorization through its authenticated backend using the receipt and verifier. Browser possession alone grants no Connection-management or completion authority. Cross-tab substitution, altered return URLs, another initiating principal, and a wrong verifier fail closed. The application customer never needs to log in to Console.

The launch token is carried in a fragment and removed from browser navigation state before external navigation. Browser pages and callback responses use no-store and no-referrer policies. Return URLs cannot contain credentials or fragments. Receipts, provider URLs, callback codes, and verifier material are never model-visible inputs, audit details, or ordinary Connection metadata.

## Connection checks

`POST /connections/{id}/check` performs one bounded remote observation under the exact management version. `last_check` records its time, scope (`provider_account` or `mcp_discovery`), result (`passed`, `action_required`, or `unavailable`), and safe error code. A provider-account check verifies the exact bound account; an MCP check verifies initialization and tool discovery. Neither implies that every future tool call will succeed.

Checks cannot restore an old binding while authorization is active. Publishing an observation rechecks current management authority, Connection version and authorization generation, and the exact Provider's active credential generation. Transport failure is an unavailable observation, not proof of revocation. Checks never change an accepted Run's tool selection or authorize a substitute source.

## Assignment and effective selection

[Agent configuration](../28-agent-management.md#agentconfig) and narrow AccountTarget overrides use one `connection_tools` list. Each entry names `connection_id`, the source-native `tools` selection, and `defer_loading`. Omitted or null tools means all currently available authorized tools; an empty list means none. Duplicate Connection IDs are invalid, including across source kinds.

Run acceptance resolves each Connection and freezes its kind, Connection ID, authorization generation, tool scope, and deferred-loading policy. Connector selections additionally retain the resolved Provider ID. [Run persistence](../12-run-persistence.md) owns the single `connection_selections` snapshot. These are accepted authorization facts, not a catalog of discovered tool definitions.

Every dispatch checks current authority and the accepted authorization generation. Reauthorization keeps the Connection ID usable for new Runs while old Runs fail explicitly before using the replacement authorization. Recovery, child Runs, and retries preserve the accepted generation and never silently upgrade it. Runtime discovery can refresh tool definitions within the accepted source and scope, but cannot choose another Connection or Provider.

Connector tools use one in-process a13n MCP capability per selected Connection. Remote MCP connections use independent remote clients. The [Agent-facing tools contract](04-agent-facing-tools.md) owns discovery, namespaces, bounded results, and Attempt-level dispatch authority.

## Management API

```http
POST   /api/v1/workspaces/{workspace}/connections
GET    /api/v1/workspaces/{workspace}/connections
GET    /api/v1/connections/{connection_id}
PATCH  /api/v1/connections/{connection_id}
POST   /api/v1/connections/{connection_id}/enable
POST   /api/v1/connections/{connection_id}/disable
POST   /api/v1/connections/{connection_id}/check
DELETE /api/v1/connections/{connection_id}
POST   /api/v1/connections/{connection_id}/authorizations
GET    /api/v1/connection-authorizations/{authorization_id}
POST   /api/v1/connection-authorizations/{authorization_id}/cancel
POST   /api/v1/connection-authorizations/{authorization_id}/complete
POST   /api/v1/connection-authorizations/{authorization_id}/launch
POST   /api/v1/connection-authorizations/{authorization_id}/receive
```

Launch and receive are capability-scoped browser operations. Other operations use ordinary Service authentication and current resource authority. Protocol-specific MCP client configuration lives under `/connections/{id}/mcp/`; explicit Connector revocation lives under `/connections/{id}/connector/revoke`. Deletion immediately closes local use and returns a bounded cleanup receipt, preserving unknown remote-cleanup outcomes rather than claiming guaranteed upstream deletion.

The Provider-type catalog is deployment-scoped and read-only. Configured Provider and Connector discovery routes use the exact resource identity:

```http
GET   /api/v1/connector-provider-types
GET   /api/v1/organizations/{organization}/connector-providers
POST  /api/v1/organizations/{organization}/connector-providers
GET   /api/v1/workspaces/{workspace}/connector-providers
POST  /api/v1/workspaces/{workspace}/connector-providers
GET   /api/v1/connector-providers/{connector_provider_id}
PATCH /api/v1/connector-providers/{connector_provider_id}
POST  /api/v1/connector-providers/{connector_provider_id}/test
POST  /api/v1/connector-providers/{connector_provider_id}/discover-connectors
GET   /api/v1/connector-providers/{connector_provider_id}/connectors/{connector_key}/tools
```

Provider creation accepts `type`, `configuration`, and separate write-only service credentials validated by `credential_schema` when the selected implementation requires them. Unknown types and configuration fields fail validation. Type, endpoint, and behavior-defining configuration changes require another Provider; mutable updates retain the existing exact management-version precondition. A Provider PATCH may combine name, status, and write-only credentials under one expected version. All supplied changes commit atomically and advance the management version once. Credential replacement also remains available through the owning management operation and atomically replaces the Provider-owned encrypted bundle. Testing and discovery are explicit bounded operations outside database transactions and never create Connector Connections. A successful test reports `verified_access` (`catalog_read` or `account_read`) for the operations actually checked; it does not imply untested credential permissions. Idempotent replay returns that same diagnostic.

Type-definition and Connector discovery reads require the safe-read authority defined by [IAM](../33-identity-and-access-management.md#stable-action-registry); Provider management and testing require `connector_provider.manage`. Discovery additionally checks the exact Provider's Workspace visibility, current active status, and service credential eligibility. A response contains safe metadata only and never a credential value, external account reference, or import target.

## Invariants

1. There is one Connection resource and one public authorization operation model for Connector and MCP sources.
2. Applications own their end-user mapping; Service Accounts can manage and complete authorization without Console login.
3. Creating a Connection never authorizes an account or invokes a tool.
4. Source identity is immutable; confirmed reauthorization can change the upstream account and advances authorization generation.
5. No old Run, retry, or child execution silently adopts a replacement authorization.
6. Normal token refresh does not invalidate an accepted authorization generation.
7. Browser completion requires the original principal, browser binding, receipt, and backend-held verifier.
8. Provider and credential material remain private; safe observations never grant execution authority.
9. Connections supply tools only through Agent Runs; Service owns no parallel tool-execution API.
