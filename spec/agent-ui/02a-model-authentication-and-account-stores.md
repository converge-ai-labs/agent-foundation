# Model Authentication and Compatible Account Stores

## Design Position

Agent UI supports API-key Models and OAuth subscription-backed Models. The shared [Harness Model Authentication contract](../agent-harness/16a-model-authentication.md) owns provider credential values, source protocols, OAuth exchange and refresh behavior, request injection, single-flight, replay, and native Model construction. Agent UI is the local Host: it selects authentication in Model resources and adapts Codex and Grok Build product stores to those SDK-first source protocols.

Codex and Grok subscription authentication reuse the upstream products' account stores instead of creating another Agent UI token copy. A user who already authenticated with Codex or Grok Build should normally run the corresponding Model without another browser login. A login started by Agent UI writes through the same compatible product store so the upstream CLI can reuse it.

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

The Agent UI release-owned Model integration selected by the route declares which authentication kinds it accepts. A route cannot silently reinterpret an incompatible credential or fall back from a selected subscription account to an ambient API key.

## Ownership

| Concern                                                                                                          | Owner                              |
| ---------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| Credential types and `load()` / `save()` protocols                                                               | Harness `model_auth`               |
| OAuth browser/device exchange, refresh, expiry, process-local single-flight, request headers, and one 401 replay | Harness `model_auth`               |
| Codex Responses subscription dialect and Grok native Model construction                                          | Harness `model_auth`               |
| Effective local product-store policy and path                                                                    | Agent UI provider adapter          |
| Product file parsing, schema preservation, advisory locking, and optimistic digest checks                        | Agent UI provider adapter          |
| Local account inspection, login confirmation, and logout surfaces                                                | Agent UI                           |
| Durable managed service storage and distributed coordination                                                     | Foundation Service or another Host |

Agent UI does not wrap Harness authentication with another token callback or Model HTTP-auth layer. Its account stores directly implement the corresponding Harness credential-source protocol.

## Compatible Product Stores

| Authentication kind  | Preferred interoperable file profile                                                            | Interoperability requirement                                           |
| -------------------- | ----------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `codex_subscription` | `$CODEX_HOME/auth.json`, with the upstream default under `~/.codex/`                            | Follow the reviewed Codex credential-store policy and `auth.json` form |
| `grok_subscription`  | `$GROK_AUTH_PATH` when set; otherwise `$GROK_HOME/auth.json`, defaulting to `~/.grok/auth.json` | Follow the reviewed Grok Build scoped `auth.json` contract             |

The compatible file profile is preferred because both Agent UI and the upstream CLI can inspect and update one product-owned location. Agent UI does not copy these credentials into `~/.a13n-ui`, SQLite, immutable objects, configuration generations, or an Agent UI-owned keyring. It does not invent a combined Codex/Grok schema.

The two products do not share one JSON schema. Codex stores one auth envelope containing its selected mode and token set. Grok Build stores entries keyed by resolved authentication scope. Each adapter preserves unrelated supported fields and scopes.

A physical `auth.json` file is not unconditionally authoritative. The adapter first resolves the upstream product's effective credential-store policy. Codex can select file, keyring, automatic, or ephemeral storage. Grok Build can override the file path and can receive process-supplied credentials that are not a writable shared login store. Agent UI follows the supported file policy or reports the selected mode as unsupported and requires the user or embedding Host to switch the upstream product policy explicitly; it never claims that an in-memory selection changed upstream configuration, merges stores, or creates a shadow credential source.

## Credential Source Behavior

For every Harness `load()`, the adapter rereads the selected product store and returns one complete credential set. It does not retain a long-lived token snapshot. A missing, malformed, incompatible, or differently scoped store fails explicitly.

For every Harness `save()`, the adapter:

1. requires a matching account and provider scope;
2. preserves unrelated supported document fields;
3. retains provider fields not represented by the Harness credential value where compatibility requires them;
4. writes with the product-compatible permissions and atomic replacement behavior;
5. verifies the observed content digest under an Agent UI advisory lock immediately before atomic replacement; and
6. fails without overwrite when a change is visible at that verification point.

The Harness reloads before refresh and adopts a changed same-account credential. Agent UI's source adds an optimistic local no-clobber check during `save()` without adding a storage-specific revision API to Harness. The product CLIs do not share an established lock protocol with Agent UI, so this check cannot provide strict compare-and-swap against a non-cooperating writer in the interval between verification and replacement. Strict multi-process coordination requires a Host-controlled store rather than a shared compatibility file.

## Reuse, Login, and Logout

Agent UI resolves local account use in this order:

1. resolve the canonical product home and effective compatible store policy;
2. let the Harness Model load and reuse or refresh the selected account for requests;
3. start interactive login only for an explicit login operation; and
4. persist a successful login through the same product adapter before reporting success.

An expiring token with a refresh grant does not start another interactive login. Authentication rejection never starts browser login. Login is itself explicit reauthentication; it can replace the same account without another flag. Replacing a different shared account requires the caller's `allow_account_switch` confirmation and otherwise fails after authorization without changing the store.

Host surfaces use the same typed operations to inspect compatible accounts, invoke a registered provider login, confirm account replacement, and log out. Agent UI natively registers Harness Codex and Grok login primitives for its executable surfaces; an embedding Host can replace these collaborators but a surface must not expose a login action when its App has no registered flow.

### Grok Scope and First Login

A Grok compatible store key is `<issuer-without-trailing-slash>::<client-id>`. On App startup, one existing OAuth scope is selected when it is unambiguous. Multiple compatible scopes require an explicit embedding selection and never produce an arbitrary winner.

When no Grok OAuth scope exists, the Agent UI executable prepares the reviewed production Grok Build profile:

- issuer `https://auth.x.ai`;
- public-client ID `b1a00492-073a-47ea-816f-4c329264a828`; and
- scopes `openid profile email offline_access grok-cli:access api:access conversations:read conversations:write workspaces:read workspaces:write`.

This default creates only the matching in-memory adapter. No file entry exists until authorization succeeds. An existing custom scope retains its issuer and client identity; Agent UI does not silently rewrite it to the production profile. Native login uses the Harness provider-specific flow against that resolved identity.

### Browser and Device Presentation

Grok browser login discovers OIDC metadata, binds a random available `127.0.0.1` callback port, creates an authorization-code plus PKCE S256 flow with state and nonce, and waits at most ten minutes for the exact callback. Agent UI writes the authorization URL and progress to stderr and may ask the operating system to open it; failure to open a browser leaves the copyable URL usable. The callback and token exchange complete before the product-store write.

`auth login grok --device-code` selects native RFC 8628 device authorization. Agent UI writes the validated verification URL, user code, and waiting progress to stderr, optionally opens the URL, and delegates bounded polling to Harness. `--device-code` is rejected for Codex. The device code, browser authorization code, PKCE verifier, OAuth tokens, and raw claims never use stdout or stderr.

### CLI Contract

```text
a13n-ui auth status [codex|grok]
a13n-ui auth login <codex|grok> [--allow-account-switch] [--device-code]
a13n-ui auth logout <codex|grok>
```

`status` without a provider returns both provider projections in stable `codex`, then `grok` order; selecting a provider returns one. `login` and `logout` require a provider. Login uses browser authorization unless Grok device authorization is explicitly selected. Logout removes only the selected compatible provider record or Grok scope and preserves unrelated document fields and scopes.

Every command supports detached text and JSON result rendering. Authorization progress and URLs use stderr in both formats; the final credential-free projection uses stdout. Login cancellation or failure exits nonzero and leaves the previous shared account unchanged.

## Setup Discovery

[First-use setup](06-setup-and-environment-readiness.md) inspects credential-free status and offers every usable compatible provider as an independently selectable starter. Selecting both creates two Model/Agent resources and one explicit default, not a combined credential or runtime fallback. Discovery never starts login or refresh. A missing or invalid provider does not prevent selecting the other provider or an existing configured Agent.

## Run Capture and Information Boundary

An immutable Run composition records the Model route and authentication kind, never credential bytes. Every independent Run receives fresh Model collaborators built by `a13n_harness.model_auth`. The local store can rotate without changing the logical composition.

Authentication diagnostics expose only bounded provider, account-status, expiry-status, and required-action facts. Tokens, authorization codes, PKCE verifier values, raw identity claims, and complete account-store content never enter model context, SQLite, immutable objects, logs, telemetry, or UI error payloads.

## Failure Semantics

| Failure                                                        | Outcome                                                                             |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Compatible account is absent                                   | Run fails with explicit authentication-required semantics                           |
| Store is malformed or incompatible                             | Read or login fails without overwrite                                               |
| Selected upstream backend cannot be shared safely              | Authentication is reported unsupported; no shadow credential is created             |
| Concurrent process publishes a newer credential before refresh | Harness adopts it when the account identity matches                                 |
| A source change is observed at the pre-replace digest check    | Agent UI fails the save without overwrite                                           |
| Reloaded credential belongs to another account                 | The current operation fails without silently switching the Run account              |
| Refresh or persistence fails                                   | The Model request fails; interactive login is not started                           |
| Explicit login would replace a different shared account        | `--allow-account-switch` confirmation is required before the compatible write       |
| Grok store has no existing OAuth scope                         | Native login uses the reviewed production profile and creates it only after success |
| Grok store has multiple compatible OAuth scopes                | Startup or account use fails until an embedding Host selects one explicitly         |
| Browser callback, OIDC validation, or device polling fails     | Login exits nonzero and leaves the previous compatible store unchanged              |

## Compatibility

Fixture-based tests use upstream-shaped stores without real credentials. They cover reading, writing, preserving unrelated records, adopting a sibling refresh, enforcing file permissions, and rejecting incompatible data or changes observed before replacement.

Provider file schemas, path policy, and login presentation may evolve with upstream products. The SDK-first Harness source and lifecycle contract remains independent from those local storage changes.

## Invariants

01. Codex and Grok subscription Models use Harness `model_auth` and prefer an existing compatible product login.
02. Agent UI account stores implement Harness credential sources; Agent UI owns no duplicate request-auth or refresh lifecycle.
03. Agent UI-originated login writes the corresponding compatible product store.
04. Effective provider policy selects one source; stores are never merged.
05. Refresh saves use an optimistic digest check plus product-compatible atomic replacement and preserve unrelated fields.
06. Authentication kind is explicit and never falls back across providers.
07. Run compositions capture authentication provenance, never credential bytes.
08. Unknown or incompatible account stores fail without overwrite.
09. Agent UI executable surfaces natively support Codex and Grok browser login; Grok alone also supports explicit device-code login.
10. A first Grok login uses the reviewed production profile, while an existing unambiguous compatible scope retains its own issuer and client identity.
11. Auth command results and diagnostics never expose token material, authorization codes, device codes, PKCE values, or raw identity claims.
