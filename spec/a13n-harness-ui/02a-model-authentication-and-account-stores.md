# Model Authentication and Compatible Account Stores

## Design Position

Harness UI supports API-key Models and OAuth subscription-backed Models. The shared [Harness Model Authentication contract](../a13n-harness/16a-model-authentication.md) defines the boundary between the official Pydantic AI Codex provider, Harness supplemental Codex request/login behavior, Harness-owned Grok authentication, and request-fresh authentication around Pydantic AI's native Copilot provider. Harness UI is the local Host: it selects authentication in Model resources and adapts Codex and Grok Build product stores to those SDK-first source protocols.

Codex and Grok subscription authentication reuse the upstream products' account stores instead of creating another Harness UI token copy. A user who already authenticated with Codex or Grok Build should normally run the corresponding Model without another browser login. A login started by Harness UI writes through the same compatible product store so the upstream CLI can reuse it.

Copilot uses the same account and Model-authoring surfaces, with a separate storage policy: native login publishes Host-owned OAuth credentials, while supported official CLI file credentials are reused in place without copying them. Its durable source/account selection and compatibility limits are defined below.

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
    env: str | None = None
    credential_ref: str | None = None  # exactly one source


class CodexSubscriptionAuthentication(BaseModel):
    kind: Literal["codex_subscription"]


class GrokSubscriptionAuthentication(BaseModel):
    kind: Literal["grok_subscription"]


class ChatGPTSubscriptionAuthentication(BaseModel):
    kind: Literal["chatgpt_subscription"]


class CopilotSubscriptionAuthentication(BaseModel):
    kind: Literal["copilot_subscription"]


ModelAuthentication = (
    ApiKeyAuthentication
    | CodexSubscriptionAuthentication
    | GrokSubscriptionAuthentication
    | CopilotSubscriptionAuthentication
    | ChatGPTSubscriptionAuthentication
)
```

The Harness UI release-owned Model integration selected by the route declares which authentication kinds it accepts. A route cannot silently reinterpret an incompatible credential or fall back from a selected subscription account to an ambient API key.

## Ownership

| Concern                                                                                             | Owner                        |
| --------------------------------------------------------------------------------------------------- | ---------------------------- |
| Codex model credentials, source protocol, refresh, single-flight, 401 replay, and Responses dialect | Pydantic AI                  |
| Codex browser PKCE and callback handling                                                            | Pydantic AI                  |
| Codex request affinity, device login, and ID-token-preserving login exchange                        | Harness Model modules        |
| Grok credential/source types, OAuth, refresh, and Model construction                                | Harness Model modules        |
| Effective product-store policy/path and schema-preserving writes                                    | Harness UI provider adapter  |
| Per-provider account binding, confirmed reset identity, and local account surfaces                  | Harness UI                   |
| Durable managed storage and distributed coordination                                                | a13n Service or another Host |

Harness UI implements the official Codex source protocol directly, with a per-provider account-binding adapter around the selected source. It does not install another token manager. Grok uses the Harness source protocol.

## Compatible Product Stores

| Authentication kind  | Preferred interoperable file profile                                                            | Interoperability requirement                                           |
| -------------------- | ----------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `codex_subscription` | `$CODEX_HOME/auth.json`, with the upstream default under `~/.codex/`                            | Follow the reviewed Codex credential-store policy and `auth.json` form |
| `grok_subscription`  | `$GROK_AUTH_PATH` when set; otherwise `$GROK_HOME/auth.json`, defaulting to `~/.grok/auth.json` | Follow the reviewed Grok Build scoped `auth.json` contract             |

The compatible file profile is preferred because both Harness UI and the upstream CLI can inspect and update one product-owned location. Harness UI does not copy these credentials into `~/.a13n-harness-ui`, SQLite, immutable objects, configuration generations, or a Harness UI-owned keyring. It does not invent a combined Codex/Grok schema.

The two products do not share one JSON schema. Codex stores one auth envelope containing its selected mode and token set. Grok Build stores entries keyed by resolved authentication scope. Each adapter preserves unrelated supported fields and scopes.

A physical `auth.json` file is not unconditionally authoritative. The adapter first resolves the upstream product's effective credential-store policy. Codex can select file, keyring, automatic, or ephemeral storage. Grok Build can override the file path and can receive process-supplied credentials that are not a writable shared login store. Harness UI follows the supported file policy or reports the selected mode as unsupported and requires the user or embedding Host to switch the upstream product policy explicitly; it never claims that an in-memory selection changed upstream configuration, merges stores, or creates a shadow credential source.

## Copilot Accounts and Source Selection

`copilot_subscription` requires a `github-copilot:` route and has no configurable subscription endpoint. The native Pydantic AI device flow uses the official Copilot CLI public App ID `Ov23ctDVkRmgkPke0Mmm`; users do not register an application or provide a client secret. The requested `read:user,read:org,repo,gist` scopes are the inspected historical CLI compatibility baseline, not a claim of minimal permissions or current subscription entitlement. Login presentation warns about broad repository permissions. Browser-callback login is unsupported. GitHub authorization identifies the account through `/user`; it does not make an inference request.

The Host data root owns `oauth/copilot.json`, a version-1 document containing native `accounts`, the selected GitHub login, and its source binding. Native entries contain the upstream complete credential envelope and once-normalized absolute expiries. The binding names either `native` or `copilot_cli_file`; a CLI binding also retains the canonical config-file path. This plaintext store is separate from API-key `auth.json`, configuration, SQLite, immutable objects, and continuation state. Writes use private permissions, fsync, atomic replacement, and observed-digest checks while retaining other accounts and metadata. Native rotation uses the same durable exclusion and uncertainty rules as Grok. Both replacement tokens must be published before they can be used.

When no selection exists, the Host can bind the official CLI's selected GitHub.com user without copying its credentials. Discovery uses `$COPILOT_HOME/config.json`, or `~/.copilot/config.json`, only when the sibling `settings.json` explicitly selects `storeTokenPlaintext: true`. The initial user is `lastLoggedInUser`, otherwise `loggedInUsers[0]`. A selected user with missing credentials does not trigger a search for another token. Once bound, later CLI active-user changes, missing credentials, expiration, configuration-path changes, and logout do not select a different account or source. Explicit native login or source selection changes the binding; an existing Model resource is not rewritten.

The supported file profile accepts JSON comments and the reviewed snake_case aliases for these top-level fields. Token keys are the full `https://github.com:<login>` string. A nonempty `copilotTokens[key]` string takes priority over the supported `authTokens[key] = {"token": "..."}` envelope. Unsupported envelope fields fail rather than inventing refresh grants or expiration. File tokens have unknown expiry and cannot be refreshed by Harness UI. The current OS-keychain encoding and precedence are not supported; absent/false plaintext policy never authorizes a stale-file fallback. Native login remains available independently of unsupported external storage. This file-reader compatibility is bounded to the inspected official CLI 1.0.88 Linux fixtures; it is not a guarantee for other versions, platforms, or login writers.

Authenticated `GET /api/auth/accounts/{provider}/sources` lists nonsecret candidates and `PUT /api/auth/accounts/{provider}/selection` explicitly selects `{source, account_id}`. The server resolves the path; clients cannot submit an arbitrary credential-file path. CLI `auth sources` and `auth select`, terminal setup, and browser account cards use the same operations. Diagnostics include account identity, source identity, and whether deletion affects the shared CLI source. Model authentication pins both account and source for the provider lifetime and fails if either changes.

Logout deletes the selected native entry or both accepted token fields for the selected CLI user, preserving other accounts and unrelated fields/files. CLI-file writes normalize JSONC to JSON. The nonsecret binding remains and can report missing credentials; there is no disabled/disconnect marker and no fallback. Logging into that exact source/account externally can make it usable again. Web logout requires confirmation warning that shared-file deletion affects the official CLI and other Hosts; the explicit CLI logout command needs no second confirmation. Logout is local deletion, not GitHub revocation. A native login whose observed Host or selected external store changed during authorization fails without publishing its result. Non-cooperating official CLI writers retain the optimistic no-clobber limitation described above.

Authenticated model discovery is a separate explicit `POST /api/auth/accounts/copilot/models` operation. It requests `/models` from the native provider, returns only unique IDs advertising `/chat/completions`, does not follow server pagination URLs, and rejects results after a source/account change. It is not the public model directory, an inference probe, a settings recommendation, or proof of entitlement. Custom model IDs remain editable when discovery is unavailable.

## ChatGPT Subscription Accounts

`chatgpt_subscription` requires `openai-chatgpt:<model_id>` and uses the shared [ChatGPT protocol and Model dialect](../a13n-harness/16a-model-authentication.md). It is independent of Codex and never reads or modifies `~/.codex`. No static API key or configurable endpoint is accepted.

The Host data root's `auth.json` owns the stable host identity, selected issued client registration, complete credentials, and a bounded pending PKCE attempt in `openai_chatgpt`. Registration identity and subject survive logout. Explicit account selection or new registration changes the binding; active Model providers reject a changed account or host. Inspect and candidate projections contain identity, expiry and required action, never tokens or private callback material. The Host serializes grant spend and publication across cooperating processes; durable grant fingerprints prevent replay after an uncertain outcome or interruption. Both rotated tokens are published before use. The file remains plaintext with private permissions and is not a keyring.

Full callback URL paste is the primary TUI and one-shot CLI path. Browser WebUI defaults to an automatic loopback listener and offers paste as a secondary path, including when the Host is remote. Both paths validate the same exact loopback redirect, state, expiry and issued client and use the same exchange; a pasted URL is never fetched. A valid callback consumes the durable pending attempt before token exchange. Invalid input leaves it retryable; exchange failure requires a new attempt. A successful or cancelled session clears callback material. Logout clears local tokens first, retains registration metadata, and separately reports when OpenAI revocation is not confirmed.

Explicit account model discovery requests the public `/v1/models` endpoint, filters `visibility=list`, and preserves server order, slugs and display names. It is account-specific, not proof of inference entitlement. Custom model IDs remain available.

## Host-local API Keys

API-key authentication selects exactly one `env` name or `credential_ref` resource ID. A reference resolves in the Host data root's independent `auth.json`, not the configuration tree. The version-1 file contains a map of references to plaintext keys and an independent `openai_chatgpt` authorization section. One atomic document writer preserves both sections and additive metadata; API-key projections expose only key references. Harness UI supports explicit add/replace/delete and lists only reference IDs. Reads, diagnostics, exports, and frozen Run recipes never return key bytes or masked key fragments. Environment references remain supported without implicit fallback.

The Host rereads a referenced key when constructing a Model for a Run. Updating or deleting a key affects future resolution, not already constructed clients. Missing references fail before a provider request. Writes serialize across cooperating processes, preserve unrelated references, and atomically replace the file with POSIX mode `0600` in a private directory. Version-1 documents accept additive JSON metadata outside the `keys` map. Add/replace/delete preserves that metadata as well as unrelated key references; it never substitutes masked `SecretStr` display values for stored key bytes. Unsupported versions and malformed known fields fail without overwrite. API inputs remain closed contracts, and listing exposes only reference IDs, not document metadata. This is a local plaintext store, not encryption or a keyring.

## Credential Source Behavior

For every provider `load()`, the adapter rereads the selected credential store and returns one complete credential set. It does not retain a long-lived token snapshot. A missing, malformed, incompatible, or differently scoped store fails explicitly.

For each Codex save or Grok credential publication, the adapter:

1. requires a matching account and provider scope;
2. preserves unrelated supported document fields;
3. retains provider fields not represented by the provider credential value where compatibility requires them;
4. writes with the product-compatible permissions and atomic replacement behavior;
5. verifies the observed content digest under a Harness UI advisory lock immediately before atomic replacement; and
6. fails without overwrite when a change is visible at that verification point.

The provider reloads before refresh and adopts a changed same-account credential. Codex follows upstream lazy initial loading, cached access tokens, and install-before-save refresh semantics; a failed save surfaces but does not roll back provider memory. Account binding rejects a different stored or refreshed account when the source is consulted. A provider that encounters an account conflict must be reconstructed before reuse; file changes do not immediately invalidate cached tokens. Harness UI's source adds an optimistic local no-clobber check during `save()` without adding a storage-specific revision API to Harness. The product CLIs do not share an established lock protocol with Harness UI, so this check cannot provide strict compare-and-swap against a non-cooperating writer in the interval between verification and replacement. This limitation remains for non-cooperating product CLI writers.

Grok additionally holds a canonical-path, inter-process lock across reread, grant authorization, the entire exchange, and publication. Login publication and logout acquire the same lock. Before exchange it atomically writes and fsyncs a mode-0600 sidecar containing only SHA-256 grant fingerprints grouped by scope. The compatible auth file remains the sole token copy. Lock release after interruption does not restore grant eligibility: uncertain fingerprints survive fresh source instances, Models, Runs, and process restarts. A successful rotation retains the old fingerprint to prevent stale-token rollback; a successful publication that explicitly retains the same grant clears that grant's marker. Only a proven pre-dispatch failure can clear it without publication.

A blocked current grant projects `required_action=login` and cannot supply Model credentials, even if its access-token or expiry metadata changes. A new same-account grant is usable. Login/logout/account replacement preserve unrelated scopes and fields, and external edits visible at the pre-replace digest check fail publication. These guarantees cover cooperating Harness UI processes sharing the canonical store path; no distributed or atomic-CAS guarantee is made for non-cooperating CLI writers. Deleting the coordination sidecar is not a supported recovery action.

## Reuse, Login, and Logout

Codex and Grok resolve local account use in this order; Copilot follows its explicit source-binding contract above:

1. resolve the canonical product home and effective compatible store policy;
2. let the Harness Model load and reuse or refresh the selected account for requests;
3. start interactive login only for an explicit login operation; and
4. persist a successful login through the same product adapter before reporting success.

An expiring token with a refresh grant does not start another interactive login. Authentication rejection never starts browser login. Login is itself explicit reauthentication; it can replace the same account without another flag. Replacing a different shared account requires the caller's `allow_account_switch` confirmation and otherwise fails after authorization without changing the store.

Host surfaces use the same typed operations to inspect compatible accounts, invoke a registered provider login, confirm account replacement, and log out. Harness UI natively registers the Codex ID-token-preserving login adapter Grok login primitives, and the native Copilot device flow for its executable surfaces; an embedding Host can replace these collaborators but a surface must not expose a login action when its App has no registered flow.

### Grok Scope and First Login

A Grok compatible store key is `<issuer-without-trailing-slash>::<client-id>`. On App startup, one existing OAuth scope is selected when it is unambiguous. Multiple compatible scopes require an explicit embedding selection and never produce an arbitrary winner.

When no Grok OAuth scope exists, the Harness UI executable prepares the reviewed production Grok Build profile:

- issuer `https://auth.x.ai`;
- public-client ID `b1a00492-073a-47ea-816f-4c329264a828`; and
- scopes `openid profile email offline_access grok-cli:access api:access conversations:read conversations:write workspaces:read workspaces:write`.

This default creates only the matching in-memory adapter. No file entry exists until authorization succeeds. An existing custom scope retains its issuer and client identity; Harness UI does not silently rewrite it to the production profile. Native login uses the Harness provider-specific flow against that resolved identity.

### Browser and Device Presentation

Interactive and one-shot CLI authentication default to device authorization for Codex, Grok and Copilot. ChatGPT uses the full callback URL paste path described above. The Host presents a verification URL and user code; the user may open the URL in any browser. No browser is opened automatically and no local callback is needed. Grok uses RFC 8628; Codex uses its vendor-specific device-code/authorization-code exchange and registered device callback. Unsupported device authorization fails explicitly without fallback.

The auth CLI offers explicit browser and device actions. Codex browser authorization retains `http://localhost:1455/auth/callback`; Grok discovery uses its compatible loopback callback. Neither callback is rewritten to a different application origin. Browser login requires the user's browser to reach the Host's loopback listener; otherwise the user selects device authorization. Codex PKCE, state, and callback handling remain upstream-owned; its login exchange retains the real ID token required for native `auth.json`. Grok nonce and identity validation remain Harness-owned. Successful Codex login callbacks return `CodexLoginResult`, not a bare model credential. Fresh login and account switching publish its new ID token; same-account refresh preserves the stored ID token.

Interactive sessions are process-local and bounded to fifteen minutes, with starting, waiting, succeeded, failed, cancelled, and expired states. One session can be active per App, with at most sixteen recent terminal results retained. An authenticated active-session read returns that session's presentation or null, allowing the browser to recover a lost start acknowledgement or refresh without persisting OAuth presentation fields or starting another authorization. Polling and cancellation do not block conversation operations. App shutdown cancels pending authorization. Cancellation before credential publication prevents the write; once publication starts, its write and resulting projection are shielded to report the actual outcome. Persistence errors may require inspecting account status before retrying. Session reads return only presentation information, never the device secret, authorization code, PKCE verifier, OAuth tokens, or raw claims. Terminal results discard the presentation URL and user code.

### CLI Contract

```text
a13n-harness-ui auth key list [--format json]
a13n-harness-ui auth key set <reference>
a13n-harness-ui auth key delete <reference> [--yes]
a13n-harness-ui auth status [codex|grok|copilot|chatgpt]
a13n-harness-ui login <codex|grok|copilot|chatgpt> [--allow-account-switch] [--device-code|--browser]
a13n-harness-ui auth logout <codex|grok|copilot|chatgpt>
a13n-harness-ui auth sources copilot
a13n-harness-ui auth select copilot --source <native|copilot_cli_file> --account <login>
```

`status` without a provider returns provider projections in stable `codex`, `grok`, `copilot`, `chatgpt` order; selecting a provider returns one. `login` and `logout` require a provider. ChatGPT login defaults to manual callback paste; other providers default to device authorization; `--browser` explicitly selects a Host-local callback for Codex or Grok and is rejected for Copilot. Logout removes only the selected compatible provider record or Grok scope and preserves unrelated document fields and scopes.

Every command supports detached text and JSON result rendering. Authorization progress and URLs use stderr in both formats; the final credential-free projection uses stdout. Login cancellation or failure exits nonzero and leaves the previous shared account unchanged.

## Setup Discovery

[First-use setup](06-setup-and-environment-readiness.md) inspects credential-free status and offers each supported connection as an independent Model recipe. One operation creates one connection; additional Models and Agents do not combine credentials or introduce a runtime fallback. Discovery never starts login or refresh. The terminal wizard labels reusable accounts without requiring a new login. Copilot also offers explicit source/account reselection. Missing credentials offer explicit device/browser login through the same App sessions used by WebUI, rediscovery, or configuration without authentication. Login cancellation observes the actual terminal result; completed account publication is not rolled back by setup cancellation. Invalid or unsupported stores require repair rather than replacement. A missing or invalid provider does not prevent selecting the other provider or an existing configured Model.

## Subscription Usage

For a ChatGPT Model, idle `/status` and `/usage subscription` only report that subscription usage is unavailable and show the official ChatGPT usage-management URL. This display performs no account query, credential inspection, or refresh. Numeric remaining allowance and reset timestamps are not supplied; local token totals, credential expiry, and Codex windows never substitute for ChatGPT plan allowance. `/usage reset` remains Codex-only.

### Codex Subscription Usage and Reset Credits

The App reads Codex subscription usage from the supported ChatGPT account API, separately from model-token accounting and token expiry. Read-only status reports provider usage windows, usage percentages, scheduled reset timestamps, and available reset credits. Unsupported or unavailable credit APIs do not hide successfully read usage windows. Unknown fields or unavailable prices are not invented. The CLI's `/status` and `/usage subscription` refresh this information while idle without opening a selector. Each reported window shows its own remaining percentage, clamped to 0–100, a duration-derived label, and local reset time; missing windows are explicitly unavailable, not zero. `/usage reset` explicitly opens eligible credit selection.

Resetting usage is a separate explicit mutation that consumes an eligible `codex_rate_limits` reset credit. It never runs merely because the CLI reads limits, completes login, or reaches a limit. The selector shows the account and chosen credit, explains consumption, and defaults to not redeeming. Confirmation binds the account ID, credit ID, and a new redemption UUID. The App checks the request-fresh account before sending the mutation; an account switch requires a new read and confirmation.

The request uses the provider's `redeem_request_id` and `credit_id` body fields. An uncertain outcome is not reported as a confirmed failure or a restored quota; an explicit retry of the pending confirmation reuses the same identifiers. The terminal retains the pending request independently of its menu, including after cancellation, and `/usage reset` reopens that same-ID confirmation before allowing another redemption. `/status` only reports the pending identity and retry command. This recovery state is process-local; its identity is displayed so an unknown outcome is not lost silently. `reset`, `nothing_to_reset`, `no_credit`, and `already_redeemed` remain distinct outcomes. A confirmed redemption followed by failed status refresh remains a confirmed redemption. The UI never creates a replacement redemption automatically.

Each account operation constructs a fresh official Codex provider over its dedicated HTTP client and Host credential source. It uses upstream refresh/persistence semantics and one 401 authentication replay, without the OpenAI SDK transport retry layer. Redirects are disabled, requests have finite time and response-size bounds, and provider error bodies and credentials are not exposed as status diagnostics. This integration does not introduce a second credential store or refresh implementation.

## Run Capture and Information Boundary

An immutable Run composition records the Model route and authentication kind, never credential bytes. Every independent Run receives a fresh official Codex provider wrapped by `CodexRequestModel`, a fresh Harness Grok Model, or a native Copilot Model with request-fresh account/source binding. The local store can rotate without changing the logical composition.

Authentication diagnostics expose only bounded provider, account/source identity, account-status, expiry-status, and required-action facts. Tokens, authorization codes, PKCE verifier values, raw identity claims, and complete account-store content never enter model context, SQLite, immutable objects, logs, telemetry, or UI error payloads.

## Failure Semantics

| Failure                                                        | Outcome                                                                                                               |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Compatible account is absent                                   | Run fails with explicit authentication-required semantics                                                             |
| Store is malformed or incompatible                             | Read or login fails without overwrite                                                                                 |
| Selected upstream backend cannot be shared safely              | Authentication is reported unsupported; no shadow credential is created                                               |
| Concurrent process publishes a newer credential before refresh | The provider adopts it when the Host account binding matches                                                          |
| A source change is observed at the pre-replace digest check    | Harness UI fails the save without overwrite                                                                           |
| Reloaded credential belongs to another account                 | Host binding rejects the new set; Codex proactive hints may retain the cached token                                   |
| Refresh or persistence fails                                   | Upstream Codex hint/replay behavior applies; save failure surfaces with rotated memory retained; no interactive login |
| Explicit login would replace a different shared account        | `--allow-account-switch` confirmation is required before the compatible write                                         |
| Grok store has no existing OAuth scope                         | Native login uses the reviewed production profile and creates it only after success                                   |
| Grok store has multiple compatible OAuth scopes                | Startup or account use fails until an embedding Host selects one explicitly                                           |
| Browser callback, OIDC validation, or device polling fails     | Login exits nonzero and leaves the previous compatible store unchanged                                                |

## Compatibility

Fixture-based tests use upstream-shaped stores without real credentials. They cover reading, writing, preserving unrelated records, adopting a sibling refresh, enforcing file permissions, and rejecting incompatible data or changes observed before replacement.

Provider file schemas, path policy, and login presentation may evolve with upstream products. The SDK-first Harness source and lifecycle contract remains independent from those local storage changes.

## Invariants

01. Codex uses the official Pydantic AI provider with Harness supplements; Grok uses Harness authentication. Both prefer an existing compatible product login.
02. Harness UI account stores implement Harness credential sources; Harness UI owns no duplicate request-auth or refresh lifecycle.
03. Codex/Grok login writes the compatible product store; Copilot native login writes the separate Host OAuth store.
04. Effective provider policy selects one source; stores are never merged.
05. Refresh saves use an optimistic digest check plus product-compatible atomic replacement and preserve unrelated fields.
06. Authentication kind is explicit and never falls back across providers.
07. Run compositions capture authentication provenance, never credential bytes.
08. Unknown or incompatible account stores fail without overwrite.
09. All three subscription providers support device authorization. Codex/Grok also support explicit browser login with their provider-compatible redirect; Copilot does not.
10. A first Grok login uses the reviewed production profile, while an existing unambiguous compatible scope retains its own issuer and client identity.
11. Copilot binds both account and source, never copies CLI tokens, and never falls back after missing credentials or logout.
12. Auth results and diagnostics never expose token material, authorization codes, secret device codes, PKCE values, or raw identity claims; the human-facing user code is shown only during authorization.
