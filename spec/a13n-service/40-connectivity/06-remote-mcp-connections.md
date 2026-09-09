# Remote MCP Connections

## Design Position

`MCPConnection` is Service's single configuration resource for using a user-supplied Remote MCP endpoint with one authorization identity. It combines endpoint and authorization lifecycle because MCP tool availability can vary by presented authorization. The same endpoint used by two identities is represented by two MCPConnections; Service defines no separate `MCPServer` resource.

a13n Service acts as the MCP client and supports only the Streamable HTTP transport at protocol revision `2025-11-25`. Local `stdio` MCP configuration remains a direct Harness or Harness UI concern and never causes a hosted Worker to launch a user-supplied process. A server that cannot negotiate that exact revision is incompatible; Service does not silently select another revision.

Service implements the standard MCP OAuth client flow once. It does not implement provider-specific Slack, GitHub, Google, or other OAuth branches. Externally managed SaaS OAuth remains owned by the [external integration service](03-connectors-and-connections.md#credential-custody-and-setup), not this contract.

## MCPConnection

The following schema is conceptual and is not a wire or ORM model:

```python
type MCPAuthMode = Literal["none", "bearer", "oauth", "static_headers"]
type MCPConnectionStatus = Literal[
    "pending",
    "ready",
    "action_required",
    "disabled",
]
type MCPConnectionStatusReason = Literal[
    "reauthorization_required",
    "incompatible",
]


class MCPConnection:
    id: MCPConnectionId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    name: str
    endpoint_url: str
    auth_mode: MCPAuthMode
    status: MCPConnectionStatus
    status_reason: MCPConnectionStatusReason | None
    version: int
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

`endpoint_url` is one credential-free absolute Streamable HTTP MCP endpoint validated under the shared [Connectivity outbound-network policy](00-overview.md#outbound-endpoint-policy). It contains no user info, access token, API key, fragment, or model-controlled component. Redirects and resolved destinations are bounded and revalidated on every discovery, authorization, and runtime request.

Endpoint, Workspace, authentication mode, and the normalized static-header name set are immutable. Changing any of them creates another MCPConnection. Name and local disabled state are mutable under exact version preconditions. OAuth token refresh, bearer or static-header value replacement, tool discovery, health observations, and safe status reconciliation do not reinterpret endpoint identity.

Every MCPConnection belongs to its Workspace. There is no personal owner field; remote account identity is never inferred from email or display names.

`pending` means setup has not produced usable authorization and discovery. `ready` means the latest authorized setup and tool discovery succeeded, not that every later request will succeed. `action_required` blocks use until the user repairs authorization or compatibility. `disabled` is a reversible Service decision that blocks new selection and calls. `status_reason` is non-null exactly for `action_required` and is one finite safe code; it never contains remote payloads or credentials. Transient discovery or request failures do not change status. An explicit reconnect can restore `action_required` to `ready` only while the immutable endpoint, Workspace, authentication mode, and header-name identity remain unchanged; otherwise the user creates another MCPConnection.

## Ownership and Runtime Eligibility

An MCPConnection can be selected by authorized Workspace Agent defaults, direct Run overrides, or exact Account target overrides. Workspace Admin manages its lifecycle and credentials. Workspace membership alone does not grant management authority.

Every call checks the Run execution Principal and current Workspace and source eligibility. Inbound Runs use the configured Service Account, never an external sender. Connection identity and accepted tool scope remain exact.

An MCPConnection does not store mutable Agent or AccountTarget assignment lists. Agent authoring and the common [`RunCapabilityOverlay`](../28-agent-management.md#run-capability-overlay) reference the MCPConnection ID and choose tool scope and deferred loading. Authorized reads can derive reverse-use projections without creating another assignment authority.

The model never receives the endpoint URL, MCPConnection ID, authorization metadata, remote account identifiers, access token, refresh token, client registration credential, or setup handle.

## Authentication Modes

`none` sends no credential and is valid only when the Remote MCP endpoint permits anonymous access.

`bearer` accepts one opaque bearer value through the MCPConnection setup or replacement operation. Service stores it in the MCPConnection-owned encrypted bundle and sends it only in the standard authorization header to the exact endpoint. The value is never returned after acceptance.

`static_headers` accepts a bounded non-empty map of static application header names and secret values during setup. It supports endpoints that use `X-API-Key`, a custom authorization scheme, or another fixed application header. Names are ASCII, case-insensitively unique, and fixed for the MCPConnection identity; values are bounded, reject control characters and line breaks, remain write-only, and can be replaced together for rotation. Service rejects hop-by-hop, proxy, routing, cookie, content framing, origin-forwarding, and MCP protocol or session control headers. It sends accepted headers only to the exact validated endpoint origin and never forwards them across an origin-changing redirect. One MCPConnection has exactly one authentication mode, so `static_headers`, `bearer`, and `oauth` cannot be combined.

Query-string credentials, cookies, shell environment, endpoint-embedded credentials, and model- or caller-supplied per-call headers are not supported.

`oauth` follows the current MCP HTTP authorization specification. The Remote MCP endpoint acts as the protected resource, its advertised authorization service authenticates the resource owner, and Service acts as the OAuth client.

## Streamable HTTP Discovery

Service uses the Harness and upstream MCP client to initialize sessions, negotiate the supported protocol revision, discover tools, process notifications, invoke tools, and close transports. The client handles MCP session IDs, protocol headers, JSON-RPC correlation, pagination, JSON or SSE responses, and cancellation. Service adds connection-specific authorization, outbound endpoint validation, and the [discovery and result bounds](04-agent-facing-tools.md#discovery-and-result-bounds) through supported client hooks; it does not implement another JSON-RPC dispatcher, SSE parser, or session manager.

Setup and reconnect can perform bounded discovery to verify compatibility and expose safe management metadata. Runtime clients independently discover current tools under the accepted source selection. Invalid framing, unsupported protocol negotiation, authorization failures, or exceeded bounds fail explicitly. Remote notifications and caches never expand the accepted tool scope or cross authorization identities. Cancellation closes active responses and the logical session; it is not an ordinary retry.

Anonymous creation, credential replacement, and reconnect return the connection snapshot produced by successful discovery completion. Completion rechecks current management authority, connection version, and credential generation, and publishes the ready state and final idempotency receipt atomically. A completed command replays that original snapshot even if the connection later changes. While discovery has not completed, including after a failed or interrupted discovery, repeating its key returns `409 mcp_discovery_incomplete` rather than a successful pending snapshot. The caller reads the connection and can start a new reconnect command against its current version; replay never reapplies a credential replacement. Creation that requires credentials or OAuth still returns a pending connection without discovery.

## OAuth Client Flow

```mermaid
sequenceDiagram
    participant User
    participant Control as Service control
    participant MCP as Remote MCP endpoint
    participant AS as Authorization server
    participant Credentials as MCPConnection persistence

    User->>Control: connect MCP endpoint
    Control->>MCP: protected request and metadata discovery
    MCP-->>Control: challenge and protected-resource metadata
    Control->>AS: authorization-server discovery
    Control->>Control: prefer Client ID Metadata Document; otherwise DCR
    Control-->>User: browser authorization redirect with PKCE and resource
    User->>AS: authenticate and authorize
    AS-->>Control: fixed callback with code, state, and issuer
    Control->>Control: consume state and validate issuer, redirect, PKCE, and resource
    Control->>AS: exchange code
    AS-->>Control: access token and optional refresh token
    Control->>Credentials: encrypt current credential bundle
    Control->>MCP: authenticated discovery
    Control-->>User: MCPConnection ready
```

A public origin is required when interactive OAuth is used; Control can manage noninteractive connections without it. Service publishes one deployment-correct Client ID Metadata Document at `${public_origin}/api/v1/oauth/mcp/client-metadata.json`; the URL itself is the OAuth `client_id`. The document contains that exact client ID, the fixed `${public_origin}/api/v1/oauth/mcp/callback` redirect URI, the configured bounded client name, `authorization_code`, `code`, and `none` token-endpoint authentication. `public_origin` is an operator setting validated as one absolute HTTPS origin and is never derived from `Host` or forwarding headers. The document is a public protocol artifact containing no organization, MCPConnection, registration, or credential data.

Service uses Client ID Metadata when the discovered authorization server advertises it. Otherwise, Service uses standards-defined Dynamic Client Registration only when discovery provides a registration endpoint. A server supporting neither mechanism is incompatible with OAuth setup and the MCPConnection does not become ready. Authorization endpoints and client registration values come only from standards-defined discovery and registration.

The fixed callback uses short-lived, unpredictable, single-use state bound to the exact Organization, Workspace, MCPConnection, initiating User, endpoint resource, issuer expectation, redirect URI, and PKCE verifier. That setup state is durably available to any eligible control replica and is consumed atomically, so a callback cannot be replayed or completed for another MCPConnection or organization.

Service requires RFC 9728 Protected Resource Metadata, discovered from the Bearer challenge or the standard endpoint-path then root well-known locations, and verifies that its resource identifies the canonical MCP endpoint. It selects one advertised authorization server, discovers it through RFC 8414 or OpenID Connect metadata, and pins the exact issuer. It uses PKCE `S256` and sends the canonical MCP resource in both authorization and token requests. Scope comes from the authenticated challenge or protected-resource metadata rather than an arbitrary caller field. It never accepts manually supplied authorization, token, registration, or issuer endpoints and never treats self-reported display metadata as authorization identity.

Access tokens, refresh tokens, and Dynamic Client Registration credentials form one encrypted connection bundle. Deletion first clears local credentials, tombstones the connection, and fences setup/refresh. It takes minimal snapshots and makes one bounded attempt to delete each owned registration at its exact validated management URI. Failure or uncertainty yields an honest persisted cleanup receipt; replay cannot repeat the effect. There is no cleanup scheduler. Agent execution never opens an interactive browser flow.

## Exchange and Refresh Concurrency

The OAuth protocol implementation uses a maintained OAuth library for authorization, PKCE, token endpoint authentication, exchange, and refresh. Service adds MCP discovery, exact resource/issuer binding, shared persistence, and bounded endpoint-policy HTTP transport. The process owns a cookie-free HTTP pool; credentials are explicit request values and are never client defaults.

A callback atomically reserves shared single-use state before token exchange. Any control replica can complete it. Current connection version, credential generation, state claim owner/generation, deadline, and initiating User authority are checked again at commit. Expired or interrupted exchange claims become `action_required`; they never replay a possibly consumed authorization code. Failed setup requires an explicit new authorization.

Management discovery and Worker execution obtain current credentials through the same demand-driven refresh boundary. Discovery can refresh a pending connection after reconnect or OAuth completion; it does not require discovery to have already established readiness. Each handshake and paginated discovery request obtains current credentials. Discovery completion checks the latest credential generation used by its requests while retaining the original endpoint and management-version fence. Refresh occurs before authenticated use, never through a refresh-candidate scanner. A short connection transaction checks credential expiry and claims one credential generation. Claim acquisition is atomic for concurrent requests in the SQLite single-process profile and across PostgreSQL Pods; only the winner can exchange the claimed rotating refresh token. Exchange runs outside the transaction and success commits only under the same valid claim, eligible ready or pending connection, Workspace, and credential generation. Replacement, disablement, deletion, or new authorization fences late results.

A live competing claim causes a bounded wait outside any database session, followed by a read of the committed credentials. Waiting callers succeed with the rotated credentials without issuing another refresh; timeout reports temporary unavailability. A definite pre-dispatch connection or pool failure releases its claim and preserves credentials for a later demand-driven attempt. An expired abandoned claim, absent refresh token, invalid grant, or uncertain exchange requires reauthorization; Service cannot safely replay a token that may already have rotated. A tool request with an unknown effect is not replayed to repair authentication. Insufficient scope requiring consent also requires interactive reauthorization.

## Credential Boundary

MCPConnection records store ciphertext, nonce, key identifier, and credential generation under the [shared protection contract](../27-secret-management.md#protection-boundary). OAuth sessions separately protect PKCE and registration setup material. Completion, expiration, and terminal failure clear session material. Generic Secret routes cannot enumerate or mutate either bundle. Local deletion clears all encryption fields in its initial transaction.

No plaintext credential enters Agent configuration, Run state, discovered tool definitions, model context, Tool arguments, events, Items, errors, logs, traces, or tool results. The Worker resolves only the exact credential required for the current fenced RunAttempt and endpoint request.

## Tool Discovery and Run Selection

Tool discovery is authorization-dependent: two MCPConnections for the same endpoint can expose different tools. Management discovery is advisory and isolated by exact connection and current authorization. Run acceptance checks the configured resource and policy without remote discovery or a mandatory durable catalog. The executing Worker constructs a fresh Harness MCP client for each selected MCPConnection and discovers current tools directly from that endpoint.

`POST /api/v1/mcp-connections/{connection_id}/discover` accepts `expected_version` and returns `items` containing the current tool names, descriptions, input and output schemas, and annotations. It requires current MCPConnection management authority before remote I/O and rechecks authority and the exact management version before returning. Discovery runs outside database sessions, uses the same bounded client and credential path as setup, and invokes no tool. Its result is advisory rather than a durable command receipt or frozen Run catalog; an explicit later discovery can return different tools.

The conceptual accepted selection is:

```python
class MCPConnectionToolSelection:
    mcp_connection_id: MCPConnectionId
    tools: tuple[str, ...] | None
    defer_loading: bool
```

This selection is the authority for the exact MCPConnection, tool scope, and deferred-loading policy. `tools` retains the all-tools or explicit-name semantics of [Agent selection](../28-agent-management.md#agentconfig), not a frozen discovered list. Identity-defining connection fields are immutable; a mutable management CAS version is not a Run compatibility input. The [common runtime contract](04-agent-facing-tools.md#discovery-and-recovery) owns discovery, filtering, invocation guards, namespacing, and reconstruction without external schema snapshots.

The Run stores no OAuth scope string or token snapshot. Each remote operation revalidates current authority and resolves eligible authentication for the bound endpoint. The client uses supported authentication hooks for refresh; resolving headers once at logical-run creation cannot by itself satisfy per-call revocation and credential-refresh requirements. Remote server and model-provider execution are distinct: these clients run inside Service, and credentials are never forwarded to the model provider to let it execute MCP calls.

## Failure Semantics

| Condition                                                       | Outcome                                                                                                                  |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Endpoint violates outbound-network or transport policy          | Creation or discovery fails before any credential is sent                                                                |
| Protected-resource or authorization-server discovery is invalid | OAuth setup fails closed; Service does not accept caller-supplied replacement endpoints                                  |
| Neither Client ID Metadata Document nor DCR is supported        | OAuth setup reports an incompatible MCP authorization service                                                            |
| State, issuer, redirect, resource, or PKCE validation fails     | Callback is rejected and no credential or ready transition commits                                                       |
| Callback or token response is lost after a possible commit      | A completed receipt remains authoritative; an interrupted exchange requires new authorization without replaying the code |
| Refresh token is absent or refresh fails                        | Current call fails; the MCPConnection becomes `action_required` with `reauthorization_required`                          |
| Static header name or value violates the bounded policy         | Setup or replacement fails before any credential is stored or sent                                                       |
| Tool definitions change after Run acceptance                    | Current discovery stays within the accepted scope; missing tools or invalid arguments fail explicitly                    |
| Remote effect may have occurred but its result is lost          | Tool outcome follows the remote MCP tool's task, idempotency, reconciliation, or unknown-outcome semantics               |

## Compatibility and Invariants

Service validates negotiated MCP protocol compatibility through its supported upstream client. Supporting another remote MCP protocol revision preserves authorization isolation, accepted source identity and tool scope, and failure behavior; it does not require schema equality with earlier discovery. A user-supplied `stdio` process, arbitrary transport, per-call header map, or manual OAuth endpoint is outside the a13n Service contract.

1. One MCPConnection combines one Streamable HTTP endpoint and one authorization identity; Service defines no separate MCPServer resource.
2. a13n Service never launches user-configured MCP processes.
3. Service implements one standards-based MCP OAuth client using a Client ID Metadata Document when supported and DCR otherwise, without provider-specific branches.
4. Workspace MCPConnections require current execution Principal and Workspace authority; external actors cannot confer authority.
5. OAuth, bearer, and bounded static-header credentials are MCPConnection-owned encrypted bundles and never model-visible data.
6. Discovery and authenticated clients are isolated by MCPConnection identity; accepted Runs retain source selections, not immutable tool catalogs.

## Transport and Maintenance Bounds

Control and Worker construct remote MCP clients with the same configured HTTP phase limits, request deadline, refresh lease, and token-expiry skew. Initialization must negotiate exactly `2025-11-25`; other revisions fail before tool discovery. MCP endpoint requests reject redirects, including same-origin redirects, and never retain cookies. This endpoint rule is stricter than the bounded redirect handling used for OAuth metadata; it prevents changing a selected MCP endpoint during authenticated use.

Expired setup records are processed in bounded short transactions. A productive maintenance pass yields to other tasks and continues draining eligible work; the normal poll delay applies when there is no eligible work. No transaction spans a poll wait or network operation.
