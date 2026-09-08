# Model Authentication

## Design Position

The Harness provides SDK-first process-local authentication for native Models whose providers use user OAuth credentials rather than API keys. The public `a13n_harness.model_auth` feature module owns provider-compatible credential values, application-owned credential-source protocols, OAuth exchange and refresh primitives, request authentication, and native Model construction for supported subscription providers.

This boundary is designed for embedded applications and hosted workers alike. It does not treat an installed CLI or a local credential file as the primary API. A Host can connect a local product-compatible file, an encrypted service store, or another authorized durable source by implementing the same asynchronous `load()` and `save()` protocol.

## Boundaries

| Concern                                                                            | Owner                    | Contract                                                                               |
| ---------------------------------------------------------------------------------- | ------------------------ | -------------------------------------------------------------------------------------- |
| Credential value and `load()`/`save()` source protocol                             | Harness                  | Defines the provider-specific process-local seam                                       |
| Expiry check, refresh, process-local single-flight, and one 401 replay             | Harness                  | Runs inside the Model provider integration                                             |
| Provider OAuth endpoints, public-client exchange, and request dialect              | Harness                  | Remains provider-specific rather than one generic OAuth JSON shape                     |
| Durable credential storage, encryption, account selection, and authorization       | Host                     | Implements a credential source and controls who may use it                             |
| Strict multi-process or multi-replica refresh exclusion                            | Host                     | May coordinate inside `load()` and `save()`; it is not implied by a process-local lock |
| Local product-store path, schema preservation, login/logout UI, and account status | Harness UI               | Adapts Codex and Grok Build stores to the Harness source protocols                     |
| Model route and non-secret settings                                                | Host model configuration | Selects a supported Harness Model constructor or another native Model                  |

The Harness never discovers a Host account, selects a durable record, stores credential bytes in `HarnessState`, or defines a Model-account resource. MCP OAuth, Connector OAuth, and end-user sign-in are separate contracts.

## Public Credential Sources

The following Python-like definitions are conceptual public API, not serialized wire formats:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CodexCredentials:
    account_id: str
    expires_at: datetime
    access_token: str
    refresh_token: str
    id_token: str | None = None


class CodexCredentialSource(Protocol):
    async def load(self) -> CodexCredentials: ...
    async def save(self, credentials: CodexCredentials) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class GrokCredentials:
    account_id: str
    auth_mode: str
    create_time: datetime
    expires_at: datetime
    issuer: str
    client_id: str
    access_token: str
    refresh_token: str | None = None


class GrokCredentialSource(Protocol):
    async def load(self) -> GrokCredentials: ...
    async def save(self, credentials: GrokCredentials) -> None: ...
```

Credential representations exclude secret fields from `repr`. A source returns one complete current provider credential set. `save()` durably replaces that set or raises; it never reports success before the replacement meets the Host's durability policy.

The small protocol is intentionally structural. Provider lifecycle code needs no revision token or storage-specific snapshot. A Host that requires compare-and-swap, row locking, leases, or cross-replica exclusion implements that behavior behind `load()` and `save()`.

## Codex Device Authorization

`CodexDeviceAuthorizationFlow.start()` starts the reviewed vendor protocol at the Codex user-code endpoint. The public result exposes the verification URL, user code, interval, and maximum fifteen-minute lifetime, keeping the device authorization ID out of representations. Bounded polling treats HTTP 403/404 as pending. Success exchanges the returned authorization code and verifier at the token endpoint using `https://auth.openai.com/deviceauth/callback`, not the browser loopback redirect. This is not the RFC 8628 token grant used by Grok. Cancellation interrupts polling; the Host alone publishes credentials. `DeviceAuthorizationError.reason` distinguishes expired, denied, and unsupported outcomes where the provider protocol identifies them; no protocol silently starts another login method.

## Request Lifecycle

```mermaid
sequenceDiagram
    participant Model
    participant Auth as Harness model authentication
    participant Source as Host credential source
    participant OAuth as Provider OAuth endpoint
    participant API as Model endpoint

    Model->>Auth: outbound provider request
    Auth->>Source: load()
    alt credential is usable
        Source-->>Auth: current credentials
    else refresh is required
        Source-->>Auth: expiring credentials
        Auth->>Source: load() immediately before refresh
        alt source contains a newer same-account set
            Source-->>Auth: newer credentials
        else source remains unchanged
            Auth->>OAuth: refresh current grant
            OAuth-->>Auth: rotated credentials
            Auth->>Source: save(rotated credentials)
            Source-->>Auth: durable success
        end
    end
    Auth->>API: authenticated request
    alt API returns 401
        Auth->>Source: reload and adopt or refresh
        Auth->>API: replay once
    end
```

The source is consulted for every outbound provider request. This is a freshness check, not an unconditional refresh. One provider instance serializes its own refresh operation and lets concurrent requests adopt the completed result. Before spending a refresh token, it reloads the source and adopts a changed same-account credential set instead.

A refreshed credential is not installed for outbound use until `save()` succeeds. A failed save therefore fails the pending request and leaves the previously accepted in-memory set unchanged. This stricter ordering prevents an application from treating an unpersisted rotation as usable when refresh tokens can be single-use.

An HTTP 401 triggers at most one reload-or-refresh and one replay. The second response is returned without another authentication retry. Interactive authorization never starts from a Model request.

## Request Isolation and Affinity

OAuth headers are attached only to the exact HTTPS origin owned by the selected Model integration. Codex requests carry bearer authorization, the ChatGPT account identifier, and the Harness originator. Grok requests carry only their supported bearer authorization. Codex routing and turn-state headers follow the same exact-origin restriction. Redirects or reuse of a client for another origin cannot forward these values.

A caller-supplied HTTP client must be dedicated to the Model integration and have no existing authentication; the builder installs its request authentication and does not close that client. When the builder creates the client, the native Model lifecycle closes it and can recreate it on a later entry.

Codex uses the native Responses streaming dialect, disables provider storage, removes settings the subscription endpoint does not support, and does not claim local token counting. It derives Codex `session-id`, `thread-id`, and `x-client-request-id` defaults from the effective Harness Thread affinity already supplied as `x-session-id`; the value may be Host-selected for a new Thread but remains State-stable across continuation, and explicit case-insensitive header values win. The standard `openai_prompt_cache_key` remains owned by the Harness request-affinity Capability. Root and child Threads therefore receive different provider thread affinity while each Thread retains stable affinity across its own continuation.

### Codex Routing and Turn State

Every OAuth Codex Model request derives one backend routing hint from the effective request:

```http
x-codex-routing-hint: model=<model>
x-codex-routing-hint: model=<model>;tier=<service-tier>
```

The `tier` segment is present only when effective `openai_service_tier` or the unified `service_tier` is non-empty; the provider-specific setting has precedence, matching Pydantic AI's OpenAI request rendering. Caller-supplied values for `x-codex-routing-hint` are discarded case-insensitively so the selected Model and effective tier remain authoritative. This header belongs only to the ChatGPT Codex OAuth backend and is not added to generic OpenAI Responses Models.

For each Pydantic AI run, the Codex Model maintains one process-local first-write-wins `x-codex-turn-state` value. The initial request sends no turn state. The first non-empty value returned in a successful streaming response header is retained unchanged, and every later request with the same Pydantic run ID and `RunUsage` identity sends it as `x-codex-turn-state`. Later response values cannot replace it. A different run or reuse of the same run ID with a different `RunUsage` starts without state; a direct Model call without `RunContext` neither retains nor replays state. Caller-supplied turn-state headers are discarded so stale state cannot cross turns.

Thread affinity and turn state have separate lifetimes: Thread-derived headers remain stable across continuation, while `x-codex-turn-state` exists only for one live Pydantic run. The turn state is not persisted into `HarnessState`, messages, events, logs, or telemetry.

No OAuth token, authorization code, PKCE verifier, raw identity claim, complete source record, or Codex turn-state value enters Model messages, `HarnessState`, event payloads, logs, or telemetry.

## Codex Account Request Authentication

`build_codex_account_auth` exposes the same request-fresh credential lifecycle to Host-owned ChatGPT account API clients without constructing a Model. The caller owns the dedicated async client and disables redirects. Authentication attaches bearer and account headers only to the exact ChatGPT HTTPS origin, persists refresh before use, and permits at most one 401 reload-or-refresh replay. It does not retry timeouts or ambiguous provider outcomes.

A caller may bind an expected account ID to a confirmed account operation. A different request-fresh account fails before that operation is sent. Mutating account operations retain caller-owned stable idempotency identities across the possible authentication replay. The Harness owns authentication, not quota eligibility, reset-credit selection, confirmation UI, or provider reset outcomes.

## OAuth Flows

The module exposes provider-specific authorization and refresh primitives. `CodexOAuthFlow` is an authorization-code plus PKCE context: construction performs no I/O, `authorization_url()` creates the provider URL, and `exchange_code()` exchanges a callback code for `CodexCredentials`. A convenience one-shot localhost callback receiver is process-local; opening a browser and deciding whether and where to save the returned credentials remain caller-owned.

`GrokOAuthFlow` is a discovered OIDC authorization-code plus PKCE context. Its asynchronous constructor accepts an explicit issuer, public-client ID, loopback redirect URI, and ordered scopes; loads an issuer-matching discovery document; requires secure authorization, token, and JWKS endpoints; and creates independent random state, nonce, and PKCE values. `authorization_url()` emits the discovered authorization endpoint with `response_type=code`, client identity, redirect URI, scopes, PKCE S256, state, nonce, and an optional bounded provider referrer. Caller-supplied additional parameters cannot replace these flow-owned protocol values. The one-shot callback receiver binds only the selected loopback address, accepts only the exact callback path and state, has a finite caller-visible timeout, and always closes before token exchange completes.

Browser-flow code exchange posts the authorization grant directly to the discovered token endpoint and requires an access token, refresh token, positive expiry, and ID token. The ID token is verified against the discovered signing keys and supported algorithm set, issuer, public-client audience, expiry, and flow nonce before its non-empty subject becomes the Grok account identity. An unverified browser-delivered identity claim never becomes credential authority.

`GrokDeviceAuthorizationFlow.start()` implements the provider's RFC 8628 profile for the same explicit issuer, client identity, and scopes. It posts to the issuer's device authorization endpoint and returns a context whose public projection contains only the bounded user code, HTTPS verification URI, optional HTTPS complete verification URI, expiry, and poll interval; the device code remains secret. The bounded lifetime starts when that authorization response is accepted, not when a caller later begins waiting. `wait_for_credentials()` sleeps before its first token request, bounds every token request by the remaining lifetime, polls the issuer token endpoint with the device-code grant until that expiry, retains the interval for `authorization_pending`, increases it by five seconds for `slow_down`, and fails explicitly for denial, expiry, malformed responses, or other provider errors. Loopback HTTP verification URLs are accepted only for explicit local development or tests. A successful device exchange requires a usable access token, positive expiry, and account identity derived from the ID-token subject or selected access-token principal; it does not claim browser-flow ID-token verification because the response arrives only over the direct token channel.

Grok refresh follows the issuer and client identity in `GrokCredentials`, requires a secure issuer-matching discovery document and token endpoint before sending the refresh token, and returns another complete credential set. Authorization and device-code values are short-lived process-local flow state. They are excluded from representations and never persisted by the Harness. The caller owns authorization URL presentation, optional browser opening, cancellation, and durable credential saving. Codex and Grok do not share one token-response schema or one account identity rule.

## Failure Semantics

| Failure                                                                           | Observable outcome                                                                                                       |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Source is absent, malformed, unauthorized, or unavailable                         | The Model interaction fails with bounded authentication-required or source-failure semantics                             |
| Reloaded credential belongs to another account                                    | The interaction fails; a live Model instance does not silently switch accounts                                           |
| OAuth refresh is rejected or malformed                                            | The interaction fails; interactive login is not started                                                                  |
| Rotated credentials cannot be saved                                               | The interaction fails before the rotated access token is sent                                                            |
| Another actor rotates the same account before refresh                             | The provider adopts the reloaded set instead of spending its stale refresh token                                         |
| Another actor wins during `save()`                                                | The Host source's conflict or coordination behavior determines the failure; Harness does not claim distributed exclusion |
| First Model request receives 401                                                  | Reload or refresh is attempted, then the request is replayed once                                                        |
| Replayed request receives 401                                                     | The second response is returned without another replay                                                                   |
| Codex response omits a usable turn state                                          | The current Pydantic run remains stateless until a later successful response supplies one                                |
| Grok discovery or endpoint validation fails                                       | Interactive authorization fails before presenting or exchanging a provider grant                                         |
| Grok callback state, ID-token signature, issuer, audience, nonce, or expiry fails | Browser authorization fails and produces no credentials                                                                  |
| Grok device authorization is pending or slowed down                               | Polling continues within the provider interval and bounded device-code lifetime                                          |
| Grok device authorization is denied or expires                                    | Login fails explicitly and produces no credentials                                                                       |

## Compatibility

`a13n_harness.model_auth` is the canonical public import route; its values are not duplicated at the package root. Provider-specific credential fields, OAuth wire behavior, request headers, and Model dialects are release-owned compatibility surfaces and are tested against the supported upstream products.

Adding another provider is additive only when it has an explicit credential type, source protocol, OAuth behavior, and Model transport contract. A generic OAuth document that erases provider differences is not a compatible extension.

## Invariants

01. Model OAuth is SDK-first and does not require a CLI credential store.
02. The Harness owns process-local credential refresh, single-flight, request injection, and one 401 replay.
03. The Host owns durable credential authority, authorization, and distributed coordination.
04. Every outbound authenticated request consults its source, while refresh occurs only when needed or after rejection.
05. A rotated credential is saved successfully before it can authenticate a Model request.
06. Credentials are sent only to the selected provider's exact HTTPS origin.
07. A Model request never starts interactive login or silently changes account identity.
08. Credential bytes and Codex turn state never enter Harness continuation, Model context, events, logs, or telemetry.
09. Codex routing is derived from the effective Model and tier, while turn state is first-write-wins and isolated to one live Pydantic run.
10. Grok browser login uses discovered OIDC endpoints, PKCE, state, nonce, and verified ID-token identity; device login uses bounded RFC 8628 polling.
11. Interactive flow state remains process-local, secret-bearing values are excluded from representations, and only the Host decides whether to persist successful credentials.
