# Model Authentication and Compatible Account Stores

## Design Position

Agent UI supports both API-key Models and OAuth subscription-backed Models. Codex and Grok subscription authentication reuse the upstream products' account stores instead of creating another Agent UI token copy. A user who already authenticated with Codex or Grok Build should normally run the corresponding Model without another browser login. A login started by Agent UI writes through the same compatible product store so the upstream CLI can reuse its OAuth access and refresh credentials.

This document fixes the interoperability and ownership rules. Exact OAuth endpoints, client identifiers, scopes, token fields, browser or device-code mechanics, and upstream schema adapters remain implementation details confirmed against upstream Codex and Grok Build behavior when implemented.

## Model Authentication Selection

A Model resource selects exactly one authentication kind:

```yaml
schema_version: "1"
kind: model
id: model-codex
name: Codex Subscription
route: openai-codex:gpt-5-codex
authentication:
  kind: codex_subscription
settings: {}
model_configuration: {}
```

The conceptual union is:

```python
class ApiKeyAuthentication(BaseModel):
    kind: Literal["api_key"]
    env: str


class CodexSubscriptionAuthentication(BaseModel):
    kind: Literal["codex_subscription"]


class GrokSubscriptionAuthentication(BaseModel):
    kind: Literal["grok_subscription"]


ModelAuthentication = (
    ApiKeyAuthentication
    | CodexSubscriptionAuthentication
    | GrokSubscriptionAuthentication
)
```

The Agent UI release-owned Model integration selected by the route declares which authentication kinds it accepts. A route cannot silently reinterpret an incompatible credential or fall back from a selected subscription account to an ambient API key. Additional provider account kinds require an explicit compatible-store contract rather than a generic OAuth JSON shape.

## Compatible Product Stores

| Authentication kind  | Preferred interoperable file profile                                                            | Interoperability requirement                                           |
| -------------------- | ----------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `codex_subscription` | `$CODEX_HOME/auth.json`, with the upstream default under `~/.codex/`                            | Follow the reviewed Codex credential-store policy and `auth.json` form |
| `grok_subscription`  | `$GROK_AUTH_PATH` when set; otherwise `$GROK_HOME/auth.json`, defaulting to `~/.grok/auth.json` | Follow the reviewed Grok Build scoped `auth.json` contract             |

The compatible file profile is preferred because both Agent UI and the upstream CLI can inspect and update one product-owned location. The environment-specific home resolution and upstream default are part of compatibility. Agent UI does not copy these credentials into `~/.a13n-ui`, SQLite, immutable objects, configuration generations, or an Agent UI-owned keyring. It does not invent a combined Codex/Grok schema.

The two products do not share one JSON schema. Codex stores one auth envelope containing its selected mode and token set. Grok Build stores entries keyed by its resolved authentication scope, with each OAuth entry carrying the access credential, optional refresh token, expiry, issuer, and client identity needed by its flow. Agent UI preserves each product's own shape and unrelated supported fields.

A physical `auth.json` file is not unconditionally authoritative. A provider-specific adapter first resolves the upstream product's effective credential-store policy. Codex can select file, keyring, automatic, or ephemeral storage. Grok Build can override the file path and can receive process-supplied credentials that are not a writable shared login store. Agent UI either follows the active policy or reports it as unsupported and offers an explicit switch to the compatible file profile; it never merges stores, treats a lower-priority file as current, or creates a hidden divergent session.

Compatibility includes more than matching JSON field names. Each adapter follows the supported upstream path and store resolution, file permissions, schema preservation, token rotation, and mutation behavior. The active credential source and write target come from that product policy rather than from filename presence or the location of the preceding read.

## Reuse and Login Precedence

For a subscription-authenticated Model, Agent UI resolves authentication in this order:

1. resolve the canonical product home and effective compatible credential-store policy;
2. load the single active account selected by that policy;
3. reuse a valid account, or refresh it through the same provider policy;
4. offer an interactive login only when no reusable account exists or user action is required;
5. after login, commit the result through that policy before reporting success.

An expiring access token with a usable refresh path is not a reason to start another interactive login. Agent UI must prefer refresh and adoption of a newer credential written by a sibling Codex, Grok Build, or Agent UI process. Reloading a different account identity fails explicitly rather than silently switching the active Agent UI Run. Account switching and forced reauthentication are explicit user operations and disclose that they update the shared product account; the exact revoke-and-switch sequence remains an implementation decision.

Codex and Grok authentication are independent. Agent UI never tries one product's account store for the other product, never chooses an account by filename similarity, and never falls back across authentication kinds.

## Per-request Resolution and Automatic Refresh

Before every outbound Model request using `codex_subscription` or `grok_subscription`, the Model adapter asks the provider-specific account adapter for a currently usable OAuth access token. This is a per-request freshness check, not an unconditional refresh-token exchange: a still-valid access token outside the provider's refresh window is reused. When refresh is needed, the adapter first consults the active upstream store, adopts a newer same-account credential when available, and otherwise performs the product-compatible OAuth refresh. A successful rotation persists both the new access token and any replacement refresh token through the active store before the request proceeds.

After an authentication rejection, the adapter performs the product-supported reload and refresh recovery before surfacing failure. It does not start interactive login implicitly. Retry count, refresh window, and provider error mapping follow the implemented Codex or Grok compatibility adapter rather than a new Agent UI-wide OAuth protocol.

This matches the common shape of the reviewed implementations: Codex resolves auth for each provider request and refreshes near access-token expiry; Grok Build runs a pre-request auth path that reuses a valid token, adopts a sibling update, or refreshes through its configured OAuth/OIDC flow.

## Shared Mutation and Refresh

OAuth credentials can rotate, and another Codex, Grok Build, or Agent UI process can update the selected product store. Agent UI therefore treats the adapter-resolved store as current shared state rather than retaining a separate long-lived token copy or starting another login from stale process memory.

A provider adapter must:

1. resolve the active read source and write target through the upstream-compatible store policy;
2. reread current state before refresh or account replacement;
3. stop for an explicit account-switch operation if the current account identity changed;
4. preserve unrelated supported records or scopes rather than rewriting JSON generically;
5. adopt a newer compatible credential written by another process when possible;
6. use the upstream product's supported mutation behavior and persist a rotated credential before reporting refresh success;
7. fail without overwriting the current store when it encounters an incompatible or unreconcilable concurrent change.

These are observable no-clobber and login-reuse rules, not a new cross-product locking protocol. Exact provider-specific coordination, atomic-write, keyring, and recovery mechanisms remain implementation decisions. The configuration-tree expected-digest protocol does not govern product account stores. Agent UI configuration files contain only the authentication kind and non-secret references.

## Run Capture and Credential Lifetime

An immutable Run composition records the Model route and selected authentication kind. The Agent UI release selects the corresponding Model and account-store integration; the composition does not invent another adapter-key contract. It never records access tokens, refresh tokens, authorization codes, account-store bytes, or a digest that would turn ordinary token rotation into a composition change.

Every independent Run receives a fresh Model collaborator. Credential material is resolved for each outbound Model request. A live collaborator may reuse, refresh, or adopt a rotated credential through the selected compatible store without changing the Run's logical composition. A later request or Run sees the latest compatible account state.

Authentication diagnostics expose only bounded provider, account-status, expiry-status, and required-action facts. Tokens, authorization codes, verifier values, raw identity claims, and complete account-store content never enter model context, SQLite, immutable objects, logs, telemetry, or UI error payloads.

## Surface Behavior

CLI and WebUI expose the same typed account operations:

- inspect whether the selected compatible account is available and usable;
- start or cancel a provider-supported login;
- explicitly reauthenticate or switch account;
- log out with a warning that the shared Codex or Grok Build session is affected.

A browser is optional where the upstream provider supports a device-code or copyable-link flow. Agent UI does not require exhaustive credential-management commands merely to use an account already present on disk.

## Compatibility Boundary

Implementation adds fixture-based compatibility tests against upstream-produced stores without committing real credentials. Tests cover at least reading an upstream-produced store, writing an Agent UI login that the upstream product accepts, preserving unrelated records, adopting a sibling refresh, and rejecting an unknown incompatible schema without overwrite.

The following remain implementation decisions until those adapters are built:

- exact Codex and Grok Build schema revisions and token fields;
- browser callback versus device-code availability;
- OAuth endpoints, public client IDs, scopes, and provider headers;
- provider-specific coordination, keyring, atomic-write, and recovery mechanisms;
- explicit reauthentication and account-switch revoke ordering;
- whether supported upstream libraries can be reused directly or require narrow compatibility adapters.

These details can change with upstream behavior without changing the accepted rule that one product-compatible account store is the shared authority.

## Failure Semantics

| Failure                                              | Outcome                                                                       |
| ---------------------------------------------------- | ----------------------------------------------------------------------------- |
| Compatible account is absent                         | Run requests login or fails with an explicit authentication-required status   |
| Store is malformed or from an unsupported schema     | Read or login fails without overwriting the store                             |
| Selected upstream backend cannot be shared safely    | Authentication is reported unsupported; no shadow credential is created       |
| Active policy selects a store other than `auth.json` | Adapter follows that policy or requires an explicit switch; it does not merge |
| Concurrent process publishes a newer credential      | Adapter adopts it when valid instead of starting another login or refresh     |
| Reloaded credential belongs to another account       | Current operation fails without silently switching the Run's account          |
| Refresh cannot reconcile or persist a store change   | Current operation fails; interactive login is not started implicitly          |
| Explicit login would replace a shared account        | User confirmation is required before the provider-compatible write            |
| Credential expires during a Run                      | Adapter refreshes through the shared contract or fails that Model interaction |

## Invariants

01. Codex and Grok subscription Models use OAuth and prefer an existing compatible product login.
02. Agent UI-originated login writes the corresponding product-compatible account store.
03. Every subscription-backed Model request resolves a usable access token and automatically refreshes only when needed.
04. A rotated access or refresh token is persisted through the active compatible store before use is reported successful.
05. Agent UI owns no duplicate OAuth token authority.
06. Authentication kind is explicit and never falls back across providers.
07. Effective provider store policy selects one active credential source; stores are never merged.
08. Refresh and login reuse the upstream store behavior and never knowingly overwrite a newer compatible credential.
09. Run compositions capture authentication provenance, never credential bytes.
10. Unknown or incompatible account stores fail without overwrite.
11. Exact OAuth wire details remain implementation concerns.
