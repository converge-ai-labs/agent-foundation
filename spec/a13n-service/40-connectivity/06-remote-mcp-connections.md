# Remote MCP Connections

## Design Position

An MCP source on a managed Connection selects one Remote MCP endpoint and its authentication mode. The shared Connection and authorization contracts apply to both MCP and externally managed Connector sources; Service defines no separate mutable MCP server resource. Service does own a read-only server catalog for setup discovery. Catalog entries are templates, not Connections, credentials, availability claims, or authorization grants.

a13n Service acts as the MCP client and supports only the Streamable HTTP transport at protocol revision `2025-11-25`. Local `stdio` MCP configuration remains a direct Harness or Harness UI concern and never causes a hosted Worker to launch a user-supplied process. A server that cannot negotiate that exact revision is incompatible; Service does not silently select another revision.

Service implements the standard MCP OAuth client flow once. It does not implement provider-specific Slack, GitHub, Google, or other OAuth branches. Externally managed SaaS OAuth remains owned by the [external integration service](03-connectors-and-connections.md#credential-custody-and-setup), not this contract.

## Connection source and authority

The [Connection contract](03-connectors-and-connections.md#connection) owns identity, Workspace scope, lifecycle, authorization operations, checks, and Run generation fencing. An MCP source uses `kind: mcp`, an immutable endpoint URL and authentication mode, and fixed static header names when applicable. Several Connections can use the same endpoint with independent authorization. Endpoint and authentication-mode changes require a new Connection.

The model never receives endpoint URLs, Connection IDs, authorization metadata, upstream account identifiers, tokens, registration credentials, or browser capabilities. Service Accounts with current Connection-management authority can configure and complete OAuth without a Console identity. The application owns its customer-to-Connection mapping.

## Server catalog

`GET /api/v1/mcp-servers` exposes the deployment-resolved MCP setup catalog to authenticated callers. It supports bounded keyset pagination and case-insensitive search over key, name, and description. `GET /api/v1/mcp-servers/{server_key}` returns one entry. Each projection contains a stable key, display metadata, endpoint URL, authentication mode, optional documentation and logo URLs, setup requirements, fixed static-header names, and `builtin` or `deployment` origin. It contains no secrets and grants no authority to the endpoint.

Service ships a release-pinned built-in catalog. Operators add deployment entries with repeated `[[connectivity.mcp_servers]]` configuration. Keys are unique. A deployment entry that collides with a built-in is rejected unless it explicitly sets `override_builtin = true`; an override replaces the full entry and is projected with deployment origin. Service validates endpoint policy, browser-safe documentation and logo URLs, authentication/header consistency, and duplicate keys while composing the control runtime, so invalid catalogs fail startup. Console consumes this API and owns no duplicate MCP directory.

## Authentication Modes

`none` sends no credential and is valid only when the Remote MCP endpoint permits anonymous access.

`bearer` accepts one opaque bearer value through the Connection setup or replacement operation. Service stores it in the Connection-owned encrypted bundle and sends it only in the standard authorization header to the exact endpoint. The value is never returned after acceptance.

`static_headers` accepts a bounded non-empty map of static application header names and secret values during setup. It supports endpoints that use `X-API-Key`, a custom authorization scheme, or another fixed application header. Names are ASCII, case-insensitively unique, and fixed for the Connection identity; values are bounded, reject control characters and line breaks, remain write-only, and can be replaced together for rotation. Service rejects hop-by-hop, proxy, routing, cookie, content framing, origin-forwarding, and MCP protocol or session control headers. It sends accepted headers only to the exact validated endpoint origin and never forwards them across an origin-changing redirect. One Connection has exactly one authentication mode, so `static_headers`, `bearer`, and `oauth` cannot be combined.

Query-string credentials, cookies, shell environment, endpoint-embedded credentials, and model- or caller-supplied per-call headers are not supported.

`oauth` follows the current MCP HTTP authorization specification. The Remote MCP endpoint acts as the protected resource, its advertised authorization service authenticates the resource owner, and Service acts as the OAuth client.

## Streamable HTTP Discovery

Service uses the Harness and upstream MCP client to initialize sessions, negotiate the supported protocol revision, discover tools, process notifications, invoke tools, and close transports. The client handles MCP session IDs, protocol headers, JSON-RPC correlation, pagination, JSON or SSE responses, and cancellation. Service adds connection-specific authorization, outbound endpoint validation, and the [discovery and result bounds](04-agent-facing-tools.md#discovery-and-result-bounds) through supported client hooks; it does not implement another JSON-RPC dispatcher, SSE parser, or session manager.

Authorization and explicit checks can perform bounded discovery to verify compatibility and expose safe management metadata. Runtime clients independently discover current tools under the accepted source selection. Invalid framing, unsupported protocol negotiation, authorization failures, or exceeded bounds fail explicitly. Remote notifications and caches never expand the accepted tool scope or cross authorization identities. Cancellation closes active responses and the logical session; it is not an ordinary retry.

Every discovery completion, including management tool queries and OAuth callbacks, rechecks the initiating actor's current management authority before publishing readiness. Revoked authority leaves readiness unchanged.

Connection creation is local and returns pending. Explicit checks discover tools and may publish readiness; authorization persists the confirmed credential before bounded discovery. Temporary discovery failure leaves the confirmed authorization queryable and requests a check. Check completion revalidates current authority, version, and credential generation. Authorization replay never reapplies credentials or repeats a consumed code exchange.

## OAuth Client Flow

```mermaid
sequenceDiagram
    participant User as Application customer
    participant Control as Service control
    participant MCP as Remote MCP endpoint
    participant AS as Authorization server
    participant Credentials as Connection persistence

    User->>Control: connect MCP endpoint
    Control->>MCP: protected request and metadata discovery
    MCP-->>Control: challenge and protected-resource metadata
    Control->>AS: authorization-server discovery
    Control->>Control: use configured app; otherwise Client ID Metadata Document or DCR
    Control-->>User: browser authorization redirect with PKCE and resource
    User->>AS: authenticate and authorize
    AS-->>User: application callback with code, state, and optional issuer
    User->>Control: authenticated completion with authorization ID and provider response
    Control->>Control: validate principal, state, issuer, redirect, version, and authority
    Control->>Control: reserve the one-time exchange
    Control->>AS: exchange code
    AS-->>Control: access token and optional refresh token
    Control->>Credentials: encrypt current credential bundle
    Control->>MCP: authenticated discovery
    alt discovery succeeds
        Control-->>User: Connection ready
    else discovery is temporarily unavailable
        Control-->>User: authorization saved; retry verification
    end
```

A public origin is required only to publish Client ID Metadata Documents; Control can use pre-registered clients and noninteractive connections without it. `public_origin` is an operator setting validated as one absolute HTTPS origin, never derived from request headers. Local development can instead use plain HTTP only with the exact host `localhost`, `127.0.0.1`, or `[::1]`; alternate spellings, subdomains, and other loopback addresses are rejected. Interactive applications supply their own exact callback in `redirect_uri`. It must match the deployment allowlist `connectivity.authorization_callback_urls`; Service neither invents nor reflects an unregistered callback. For each exact discovered issuer and allowed application callback, Service derives SHA-256 issuer and redirect keys and publishes `${public_origin}/api/v1/oauth/mcp/client-metadata/{issuer_key}/{redirect_key}.json`. That URL is the OAuth client ID; its document lists only the bound application callback, plus the configured client name, authorization-code grant, code response, and public-client token authentication. This public artifact contains no Connection, Organization, principal, state, or credential data.

Client selection uses a connection-owned app when configured, then Client ID Metadata when advertised and public-client authentication is supported, then Dynamic Client Registration when available. Both a Workspace-administered app and an automatically selected or registered app are persisted independently from access tokens. A valid automatic client is reused for later authorization attempts instead of registering again. DCR selects an advertised token authentication method, preferring `none`, then `client_secret_basic`, then `client_secret_post`; omitted metadata defaults to Basic authentication. The registration response must use a supported method and supply a secret for confidential authentication. A server supporting none of these choices requires manual client configuration rather than being reported as a generic protocol incompatibility.

Workspace administrators can request setup for an exact application callback with `POST /api/v1/connections/{connection_id}/mcp/oauth-setup`. The response contains the safe current client projection and exactly one typed next action: configure an OAuth client, start user authorization, authenticate with client credentials, check the Connection, or treat setup as completed. A configure action supplies only the exact issuer, supported grants and token authentication methods, registration capability, bound callback when applicable, and catalog documentation. The application does not infer setup state by sequencing unrelated reads and discovery calls. The lower-level discovery endpoint remains an explicit safe metadata read.

Administrators can save a registered client ID, explicit grant, method, bound redirect URI, and write-only secret. Console chooses the only applicable method automatically and shows the callback URI only for the browser grant. Service revalidates the discovered issuer, grant, method, and redirect before the version-fenced write. Configuration is an internal record owned by the Connection, with its secret and any DCR management credential protected under the shared resource-credential contract. It survives token expiry and failed authorization. Replacing or clearing it atomically increments the connection version, clears active credentials, and invalidates setup and refresh claims. Replacing a Service-owned DCR client makes one bounded cleanup attempt; deletion does the same and records the result. Service never deletes a user-owned provider app and accepts no caller-defined authorization, token, registration, or resource endpoints.

The `client_credentials` grant is an explicit machine-account choice, never inferred from the presence of a secret. It has no browser redirect, callback, state, PKCE, or DCR step, and does not require an authorization endpoint in issuer metadata. Initial authentication and renewal request a token directly from the token endpoint saved during client configuration, using the saved resource as the audience rather than as an MCP request address. They do not rediscover metadata or initialize MCP while acquiring the token. Provider endpoint changes require explicit client reconfiguration, which reruns discovery and validation. An authenticated management operation obtains a token, persists it under the same connection credential boundary, and then verifies the MCP endpoint; temporary verification failure leaves the token available for an explicit check. Expiry claims the same distributed refresh fence used by authorization-code credentials, but obtains a new client-credentials token because no refresh token is required. Invalid machine credentials or an explicit authorization rejection require the administrator to repair the configured client; they do not start an interactive flow.

Management reads the safe client projection from `GET /api/v1/connections/{connection_id}/mcp/oauth-client`; `oauth-setup` is the normal setup decision endpoint and `oauth-discovery` exposes the discovery projection directly. A version-fenced `PUT` replaces or clears client configuration. The shared authorization endpoint accepts method `browser` with the same allowlisted `redirect_uri` for authorization-code clients, or `client_credentials` for the configured machine grant. A grant or redirect mismatch fails explicitly.

Short-lived unpredictable OAuth state binds the Organization, Workspace, Connection, authorization ID, initiating principal, exact resource, issuer, application redirect URI, PKCE verifier, setup generation, and Connection version. The authorization server redirects directly to the application. The application removes the provider parameters from browser-visible navigation state, verifies its locally retained state and authorization ID, authenticates to Service as the same initiating principal, and submits `state` plus exactly one of `code` or provider `error` to the shared completion operation; it also forwards `iss` when supplied. The Service validates every binding, current management authority, expiry, and replay state before reserving one exchange. Any eligible control replica can finish the operation. There is no public Service callback or browser receipt for MCP OAuth; those handoff fields remain Connector-only.

Authorization responses may omit `iss`, including when discovery advertises issuer-response support, for compatibility with existing Remote MCP providers. This is an intentional compatibility exception to RFC 9207's advertised-support requirement. Service still binds each flow to the exact discovered issuer and registered application callback. Any supplied `iss` must match exactly; an empty or conflicting issuer is never treated as absent. This policy applies equally to automatic and configured clients, without a per-Connection opt-in. PKCE, authenticated completion, expiry, principal binding, and one-time exchange checks remain mandatory.

Service requires RFC 9728 Protected Resource Metadata, discovered from a Bearer challenge or endpoint-path then root well-known locations. Missing challenges permit well-known discovery. Metadata can identify the exact endpoint or an authoritative parent resource on the same origin whose path contains the endpoint. A parent is accepted only when the metadata was read from the RFC 9728 well-known URL derived from that resource; different origins and sibling paths remain mismatches. This bounded compatibility rule covers providers that publish one origin-level OAuth resource for an MCP endpoint below it while preserving metadata-resource binding. A mismatched endpoint-path candidate does not prevent trying the root fallback. Authorization-server discovery tries RFC 8414, OIDC path insertion, then OIDC path appending. Discovery accepts an otherwise identical root issuer with or without its `/`, then pins the exact spelling declared by the authorization-server metadata for registration, callback URLs, and response validation. Different hosts, non-root paths, and non-root trailing slashes are not aliases. PKCE `S256` and the discovered resource are used in authorization and token requests. Scope comes from the Bearer challenge or protected-resource metadata, never an arbitrary caller field.

Access and refresh tokens form the encrypted connection credential bundle. Client identity and DCR management credentials live in the separate encrypted connection-owned client record, with provenance distinguishing Service-owned automatic clients from user-owned apps. Authorization sessions hold only the exact expiring continuation needed for their browser ceremony. Deletion first clears local credentials, tombstones the connection, and fences setup/refresh. It takes minimal snapshots and makes one bounded attempt to delete each owned registration at its exact validated management URI. Failure or uncertainty yields an honest persisted cleanup receipt; replay cannot repeat the effect. There is no cleanup scheduler. Agent execution never opens an interactive browser flow.

## Exchange and Refresh Concurrency

Service uses the MCP Python SDK's public helpers and models for protected-resource and authorization-server discovery, challenge parsing, Client ID Metadata, Dynamic Client Registration. Authlib performs authorization-code exchange, refresh-token exchange, and client-credentials token acquisition through the same bounded transport. Service persists browser continuation because the SDK provider keeps continuation and locking in one process and cannot resume the ceremony on another replica. Service owns exact resource/issuer binding, PostgreSQL coordination, encrypted persistence, and bounded endpoint-policy HTTP transport for both libraries. This is one lifecycle implementation with a narrow library boundary, not parallel authorization flows. The process owns a cookie-free HTTP pool; credentials are explicit request values and are never client defaults.

Authenticated completion atomically reserves the shared single-use OAuth state before token exchange. Any control replica can complete it. Current Connection version, credential generation, state claim owner/generation, deadline, redirect URI, and initiating principal authority are checked again at commit. Expired or interrupted exchange claims become `action_required`; they never replay a possibly consumed authorization code. Failed setup requires an explicit new authorization.

Management discovery and Worker execution obtain current credentials through the same demand-driven refresh boundary. Discovery can refresh a pending connection after reconnect or OAuth completion; it does not require discovery to have already established readiness. Each handshake and paginated discovery request obtains current credentials. Discovery completion checks the latest credential generation used by its requests while retaining the original endpoint and management-version fence. Refresh occurs before authenticated use, never through a refresh-candidate scanner. A short connection transaction checks credential expiry and claims one credential generation. Claim acquisition is atomic for concurrent requests in the SQLite single-process profile and across PostgreSQL Pods; only the winner can exchange the claimed rotating refresh token. Exchange runs outside the transaction and success commits only under the same valid claim, eligible ready or pending connection, Workspace, and credential generation. Replacement, disablement, deletion, or new authorization fences late results.

A live competing claim causes a bounded exponential-backoff wait with jitter outside any database session, followed by a read of the committed credentials. Waiting callers succeed with the rotated credentials without issuing another refresh; timeout reports temporary unavailability. A definite pre-dispatch connection or pool failure releases its claim and preserves credentials for a later demand-driven attempt. An expired abandoned claim, an absent refresh token for the authorization-code grant, invalid grant, or uncertain exchange requires reauthorization; Service cannot safely replay a token that may already have rotated. Client credentials instead reacquire under the same claim. A tool request with an unknown effect is not replayed to repair authentication. Insufficient scope requiring consent also requires interactive reauthorization.

## Credential Boundary

Connection records store ciphertext, nonce, key identifier, and credential generation under the [shared protection contract](../27-secret-management.md#protection-boundary). OAuth client configuration separately protects registered application secrets. OAuth sessions protect PKCE, registration setup material, received authorization codes, and receipt digests. Completion, expiration, and terminal failure clear session material. Generic Secret routes cannot enumerate or mutate these bundles. Local deletion clears all encryption fields in its initial transaction.

No plaintext credential enters Agent configuration, Run state, discovered tool definitions, model context, Tool arguments, events, Items, errors, logs, traces, or tool results. The Worker resolves only the exact credential required for the current fenced RunAttempt and endpoint request.

## Tool discovery and Run selection

The [common selection contract](03-connectors-and-connections.md#assignment-and-effective-selection) owns `connection_tools` and the accepted Connection authorization generation. Each selected MCP source produces an independent remote client. Tools are discovered under the accepted scope; server notifications cannot expand authority. Before every call, Service checks current Attempt authority and the retained authorization generation, obtains current credentials, and rechecks the version and credential generation used for dispatch. Token refresh keeps the accepted authorization generation; explicit reauthorization invalidates old selections.

Remote clients run inside Service. Endpoint credentials never go to the model provider for delegated execution. The [Agent-facing tools contract](04-agent-facing-tools.md) owns namespaces, result limits, runtime discovery, and reconstruction.

## Failure Semantics

| Condition                                                              | Outcome                                                                                                                  |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Endpoint violates outbound-network or transport policy                 | Creation or discovery fails before any credential is sent                                                                |
| Protected-resource or authorization-server discovery is invalid        | OAuth setup fails closed; Service does not accept caller-supplied replacement endpoints                                  |
| No configured client, Client ID Metadata Document, or DCR is available | OAuth discovery returns `manual`; Console requests the provider-registered client fields                                 |
| State, issuer, redirect, resource, or PKCE validation fails            | Callback is rejected and no credential or ready transition commits                                                       |
| Callback or token response is lost after a possible commit             | A completed receipt remains authoritative; an interrupted exchange requires new authorization without replaying the code |
| Authorization succeeds and verification is temporarily unavailable     | Credential remains durable and `pending`; Console offers Retry verification without another consent flow                 |
| Authorization-code refresh token is absent or refresh fails            | Current call fails; the Connection becomes `action_required` with `reauthorization_required`                             |
| Client-credentials token expires                                       | Service reacquires once under the distributed refresh claim; no browser flow or refresh token is required                |
| Static header name or value violates the bounded policy                | Setup or replacement fails before any credential is stored or sent                                                       |
| Tool definitions change after Run acceptance                           | Current discovery stays within the accepted scope; missing tools or invalid arguments fail explicitly                    |
| Remote effect may have occurred but its result is lost                 | Tool outcome follows the remote MCP tool's task, idempotency, reconciliation, or unknown-outcome semantics               |

## Compatibility and Invariants

Service validates negotiated MCP protocol compatibility through its supported upstream client. Supporting another remote MCP protocol revision preserves authorization isolation, accepted source identity and tool scope, and failure behavior; it does not require schema equality with earlier discovery. A user-supplied `stdio` process, arbitrary transport, per-call header map, or manual OAuth endpoint is outside the a13n Service contract.

1. One Connection combines one Streamable HTTP endpoint and one authorization identity; catalog entries are immutable setup templates rather than MCPServer resources.
2. a13n Service never launches user-configured MCP processes.
3. Service implements one standards-based MCP OAuth client using a configured app, Client ID Metadata Document, or DCR, without provider-specific branches.
4. Workspace Connections require execution Principal and Workspace permissions from the Attempt IAM snapshot, current resource eligibility, and live Attempt authority; external actors cannot confer authority.
5. OAuth, bearer, and bounded static-header credentials are Connection-owned encrypted bundles and never model-visible data.
6. Discovery and authenticated clients are isolated by Connection identity; accepted Runs retain source selections, not immutable tool catalogs.

## Transport and Maintenance Bounds

Control and Worker construct remote MCP clients with the same configured HTTP phase limits, request deadline, refresh lease, and token-expiry skew. Initialization must negotiate exactly `2025-11-25`; other revisions fail before tool discovery. MCP endpoint requests reject redirects, including same-origin redirects, and never retain cookies. This endpoint rule is stricter than the bounded redirect handling used for OAuth metadata; it prevents changing a selected MCP endpoint during authenticated use.

Expired setup records are processed in bounded short transactions. A productive maintenance pass yields to other tasks and continues draining eligible work; the normal poll delay applies when there is no eligible work. No transaction spans a poll wait or network operation.

The application callback handles the provider's MCP authorization response and sends it to authenticated completion exactly once. Console is one application client of that contract. The code is bounded write-only completion input passed directly to the reserved exchange; it is not persisted and never enters logs, traces, or model-visible data. Service access logs omit request query strings; application and ingress operators apply equivalent query redaction.
