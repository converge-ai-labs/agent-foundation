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

Every discovery completion, including management tool queries and OAuth callbacks, rechecks the initiating actor's current management authority before publishing readiness. Revoked authority leaves readiness unchanged.

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
    Control->>Control: use configured app; otherwise Client ID Metadata Document or DCR
    Control-->>User: browser authorization redirect with PKCE and resource
    User->>AS: authenticate and authorize
    AS-->>Control: issuer-specific callback with code, state, and issuer
    Control->>Control: validate callback and store encrypted code
    Control-->>User: same-tab completion receipt
    User->>Control: authenticated, CSRF-protected completion
    Control->>Control: reserve receipt and validate current authority
    Control->>AS: exchange code
    AS-->>Control: access token and optional refresh token
    Control->>Credentials: encrypt current credential bundle
    Control->>MCP: authenticated discovery
    Control-->>User: MCPConnection ready
```

A public origin is required when interactive OAuth is used; Control can manage noninteractive connections without it. `public_origin` is an operator setting validated as one absolute HTTPS origin, never derived from request headers. For each exact discovered issuer, Service derives `issuer_key` as its SHA-256 hex digest and publishes `${public_origin}/api/v1/oauth/mcp/client-metadata/{issuer_key}.json`. That URL is the OAuth client ID; the document lists only `${public_origin}/api/v1/oauth/mcp/callback/{issuer_key}` as its redirect URI, plus the configured client name, authorization-code grant, code response, and public-client token authentication. This public artifact contains no connection, organization, or credential data.

Client selection uses a connection-owned pre-registered app when configured, then Client ID Metadata when advertised and public-client authentication is supported, then Dynamic Client Registration when available. DCR selects an advertised token authentication method, preferring `none`, then `client_secret_basic`, then `client_secret_post`; omitted metadata defaults to Basic authentication. The registration response must use a supported method and supply a secret for confidential authentication. A server supporting none of these choices cannot complete setup.

Workspace administrators can discover the exact issuer, supported token authentication methods, and redirect URI, then save their registered client ID, method, and write-only secret. Service revalidates the discovered issuer and authentication method before the version-fenced write. Configuration is an internal record owned by the MCPConnection, with its secret protected under the shared resource-credential contract. It survives token expiry and failed authorization. Replacing or clearing it atomically increments the connection version, clears active credentials, and invalidates setup and refresh claims. Deletion clears this record but never deletes a user-owned provider app. Service accepts no caller-defined authorization, token, registration, or resource endpoints.

Short-lived unpredictable state binds the Organization, Workspace, connection, initiating User, exact resource, issuer, redirect URI, and PKCE verifier. The public GET callback validates state, the actual issuer-specific route, and issuer policy, then stores the code and a receipt digest in the encrypted setup bundle and moves `pending` to `received` without extending expiry. It redirects to Console with state and an unpredictable receipt in the URL fragment. A repeated callback cannot replace the code. Authenticated, CSRF-protected completion reserves that receipt once, rechecks the initiating User and current management authority, and exchanges only the stored code using the persisted redirect URI. Any eligible control replica can complete it.

Authorization responses may omit `iss`, including when discovery advertises issuer-response support, for compatibility with existing Remote MCP providers. This is an intentional compatibility exception to RFC 9207's advertised-support requirement. Service binds each flow to the discovered issuer, uses that issuer's distinct registered callback URI, and rejects a callback received on any other issuer's route. Any supplied `iss` must match exactly; an empty or conflicting issuer is never treated as absent. This policy applies equally to automatic and configured clients, without a per-connection opt-in. PKCE, authenticated completion, expiry, and one-time receipt checks remain mandatory.

Service requires RFC 9728 Protected Resource Metadata, discovered from a Bearer challenge or endpoint-path then root well-known locations. Missing challenges permit well-known discovery. Challenge and endpoint-path metadata must identify the exact endpoint; origin-root fallback must identify that origin. Path and trailing-slash identity are preserved; only well-known URL construction removes terminating slashes as required by the discovery specifications. Authorization-server discovery tries RFC 8414, OIDC path insertion, then OIDC path appending. Discovery accepts an otherwise identical root issuer with or without its `/`, then pins the exact spelling declared by the authorization-server metadata for registration, callback URLs, and response validation. Different hosts, non-root paths, and non-root trailing slashes are not aliases. PKCE `S256` and the discovered resource are used in authorization and token requests. Scope comes from the Bearer challenge or protected-resource metadata, never an arbitrary caller field.

Access tokens, refresh tokens, and Dynamic Client Registration credentials form one encrypted connection bundle. Deletion first clears local credentials, tombstones the connection, and fences setup/refresh. It takes minimal snapshots and makes one bounded attempt to delete each owned registration at its exact validated management URI. Failure or uncertainty yields an honest persisted cleanup receipt; replay cannot repeat the effect. There is no cleanup scheduler. Agent execution never opens an interactive browser flow.

## Exchange and Refresh Concurrency

The OAuth protocol implementation uses a maintained OAuth library for authorization, PKCE, token endpoint authentication, exchange, and refresh. Service adds MCP discovery, exact resource/issuer binding, shared persistence, and bounded endpoint-policy HTTP transport. The process owns a cookie-free HTTP pool; credentials are explicit request values and are never client defaults.

Authenticated completion atomically reserves shared single-use callback receipt state before token exchange. Any control replica can complete it. Current connection version, credential generation, state claim owner/generation, deadline, and initiating User authority are checked again at commit. Expired or interrupted exchange claims become `action_required`; they never replay a possibly consumed authorization code. Failed setup requires an explicit new authorization.

Management discovery and Worker execution obtain current credentials through the same demand-driven refresh boundary. Discovery can refresh a pending connection after reconnect or OAuth completion; it does not require discovery to have already established readiness. Each handshake and paginated discovery request obtains current credentials. Discovery completion checks the latest credential generation used by its requests while retaining the original endpoint and management-version fence. Refresh occurs before authenticated use, never through a refresh-candidate scanner. A short connection transaction checks credential expiry and claims one credential generation. Claim acquisition is atomic for concurrent requests in the SQLite single-process profile and across PostgreSQL Pods; only the winner can exchange the claimed rotating refresh token. Exchange runs outside the transaction and success commits only under the same valid claim, eligible ready or pending connection, Workspace, and credential generation. Replacement, disablement, deletion, or new authorization fences late results.

A live competing claim causes a bounded wait outside any database session, followed by a read of the committed credentials. Waiting callers succeed with the rotated credentials without issuing another refresh; timeout reports temporary unavailability. A definite pre-dispatch connection or pool failure releases its claim and preserves credentials for a later demand-driven attempt. An expired abandoned claim, absent refresh token, invalid grant, or uncertain exchange requires reauthorization; Service cannot safely replay a token that may already have rotated. A tool request with an unknown effect is not replayed to repair authentication. Insufficient scope requiring consent also requires interactive reauthorization.

## Credential Boundary

MCPConnection records store ciphertext, nonce, key identifier, and credential generation under the [shared protection contract](../27-secret-management.md#protection-boundary). OAuth client configuration separately protects registered application secrets. OAuth sessions protect PKCE, registration setup material, received authorization codes, and receipt digests. Completion, expiration, and terminal failure clear session material. Generic Secret routes cannot enumerate or mutate these bundles. Local deletion clears all encryption fields in its initial transaction.

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

The Run stores no OAuth scope string or token snapshot. Each remote operation checks the [Attempt IAM snapshot](../33-identity-and-access-management.md#attempt-iam-snapshot), accepted tool scope, current Attempt and connection eligibility, and resolves eligible authentication for the bound endpoint. The client uses supported authentication hooks for refresh; resolving headers once at logical-run creation cannot by itself satisfy per-call upstream credential revocation and refresh requirements. Remote server and model-provider execution are distinct: these clients run inside Service, and credentials are never forwarded to the model provider to let it execute MCP calls.

## Failure Semantics

| Condition                                                              | Outcome                                                                                                                  |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Endpoint violates outbound-network or transport policy                 | Creation or discovery fails before any credential is sent                                                                |
| Protected-resource or authorization-server discovery is invalid        | OAuth setup fails closed; Service does not accept caller-supplied replacement endpoints                                  |
| No configured client, Client ID Metadata Document, or DCR is available | OAuth setup reports an incompatible MCP authorization service                                                            |
| State, issuer, redirect, resource, or PKCE validation fails            | Callback is rejected and no credential or ready transition commits                                                       |
| Callback or token response is lost after a possible commit             | A completed receipt remains authoritative; an interrupted exchange requires new authorization without replaying the code |
| Refresh token is absent or refresh fails                               | Current call fails; the MCPConnection becomes `action_required` with `reauthorization_required`                          |
| Static header name or value violates the bounded policy                | Setup or replacement fails before any credential is stored or sent                                                       |
| Tool definitions change after Run acceptance                           | Current discovery stays within the accepted scope; missing tools or invalid arguments fail explicitly                    |
| Remote effect may have occurred but its result is lost                 | Tool outcome follows the remote MCP tool's task, idempotency, reconciliation, or unknown-outcome semantics               |

## Compatibility and Invariants

Service validates negotiated MCP protocol compatibility through its supported upstream client. Supporting another remote MCP protocol revision preserves authorization isolation, accepted source identity and tool scope, and failure behavior; it does not require schema equality with earlier discovery. A user-supplied `stdio` process, arbitrary transport, per-call header map, or manual OAuth endpoint is outside the a13n Service contract.

1. One MCPConnection combines one Streamable HTTP endpoint and one authorization identity; Service defines no separate MCPServer resource.
2. a13n Service never launches user-configured MCP processes.
3. Service implements one standards-based MCP OAuth client using a configured app, Client ID Metadata Document, or DCR, without provider-specific branches.
4. Workspace MCPConnections require execution Principal and Workspace permissions from the Attempt IAM snapshot, current resource eligibility, and live Attempt authority; external actors cannot confer authority.
5. OAuth, bearer, and bounded static-header credentials are MCPConnection-owned encrypted bundles and never model-visible data.
6. Discovery and authenticated clients are isolated by MCPConnection identity; accepted Runs retain source selections, not immutable tool catalogs.

## Transport and Maintenance Bounds

Control and Worker construct remote MCP clients with the same configured HTTP phase limits, request deadline, refresh lease, and token-expiry skew. Initialization must negotiate exactly `2025-11-25`; other revisions fail before tool discovery. MCP endpoint requests reject redirects, including same-origin redirects, and never retain cookies. This endpoint rule is stricter than the bounded redirect handling used for OAuth metadata; it prevents changing a selected MCP endpoint during authenticated use.

Expired setup records are processed in bounded short transactions. A productive maintenance pass yields to other tasks and continues draining eligible work; the normal poll delay applies when there is no eligible work. No transaction spans a poll wait or network operation.

Console receives state and receipt through `/mcp-setup/callback`, removes query and fragment material before authentication requests, checks same-tab state, and POSTs only `state` and `receipt` to `/api/v1/oauth/mcp/complete` with its authenticated browser session and CSRF proof. It never receives the authorization code. Service access logs omit request query strings; ingress operators must apply equivalent query redaction.
