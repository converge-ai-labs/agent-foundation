# Built-in Connector Provider Adapters

## Design Position

Composio implements the registered Connector Provider boundary. It owns a write-only service credential, establishes Workspace-bound ConnectorConnections, and contributes tools through Agent `connector_tools`, including `defer_loading`. It does not become a Service Agent runtime or expose an unfenced public execute route.

The external integration service owns every third-party account credential. Service stores only the credential used to call that ConnectorProvider, an opaque external account reference, a safe account projection and bounded operation evidence. Responses are parsed through explicit safe allowlists; credential-shaped or undocumented fields are discarded before a value reaches logging, persistence, audit, or a public response.

## Common Setup and Correlation

Creating a ConnectorConnection commits the pending Service resource and a `csa_` setup attempt before calling a ConnectorProvider. The expiring attempt binds its exact ConnectorConnection, generation, initiating Principal, Workspace, Provider type, Connector key, opaque external-user correlation, and return target. External I/O runs without an open database transaction. A short transaction records the temporary upstream setup reference and safe redirect evidence. Authoritative completion publishes the complete verified account binding on the Connection atomically. Setup history is not runtime identity.

Initial authorization has one durable sender shared by management requests and background recovery. The initial command inserts the attempt as `starting`, owns its sender lease, and records idempotency and audit evidence in the same transaction. After commit the sender calls the Provider without claiming again. Recovery acquires a new fenced lease in a separate transaction. A concurrent management replay returns the same pending attempt without a redirect while its sender is active. A different command key cannot start the same generation again: it returns `setup_already_started` without external work. An explicit reconnect starts a new generation when supported. Cancellation or lease expiry does not make a request safe to repeat: an interrupted initial request can be retried only when the Provider guarantees idempotent setup; otherwise the attempt fails with unknown-outcome evidence. A known retryable refusal can return to pending after backoff. Once a setup reference is retained, replay resumes that exact reference. Only the current unexpired sender lease may publish the initial request's result or failure.

External user correlation is a domain-separated HMAC under an operator secret over the exact Organization, Workspace, Workspace scope and ConnectorProvider identity. It contains no email, display name, raw Service ID, or credential. A ConnectorProvider response naming another correlation, provider, account, or setup generation fails closed. Only verified completion publishes the external account reference on the Connection. Before completion it belongs to the attempt. Once published, the reference is immutable; repairing a setup never substitutes another account behind the same ConnectorConnection.

Service accepts only external-service-hosted authorization or credential forms. A provider API key, password, cookie, refresh token, or other third-party account credential must travel directly from the user's browser to the ConnectorProvider. No Service management request or callback proxies such a value, even transiently.

The Composio verifier lands on a same-origin Console page:

```http
GET /connector-setup/callback?session_uri=opaque
```

Console owns this page; Service does not serve browser assets. Before setup, the authorization tab generates a 32-byte random nonce, submits its 64-character lowercase hex encoding as `browser_nonce`, and retains it in `sessionStorage`. Service stores only its SHA-256 digest on the attempt; the nonce also participates in the command fingerprint through its digest. After setup returns, the tab binds the returned `attempt_id` and expiry to that nonce. Composio receives neither the nonce nor browser state. The authorization link opens in that same tab. One tab owns one current authorization; the Service never selects the latest attempt for a user or Workspace.

The page removes `session_uri` from the address bar before starting authentication requests, retains it only in memory, and sends the following authenticated mutation with the normal Origin and CSRF proof:

```http
POST /api/v1/connector-setup/complete
Content-Type: application/json

{"attempt_id":"csa_...","browser_nonce":"<64 lowercase hex characters>","session_uri":"opaque"}
```

Control and all roles mount the completion API. It looks up the exact attempt and validates the browser digest, initiating User, current management authority, Workspace, Provider, generation, and expiry before reserving one redemption lease. External redemption and exact-account inspection happen after commit. A second fenced transaction revalidates eligibility and publishes the verified binding and `ready` together with terminal attempt and audit evidence. Successful completion returns the server-retained `return_path`. Setup accepts only an absolute local path beginning with one slash; network-path references beginning with two slashes are rejected. A completed replay returns that path only for the same authorized browser binding and current ready generation. A concurrent or uncertain redemption stays `reserved`; submitting it again never repeats redemption. Background recovery can inspect only the known account after browser verification was reserved. An `attached` attempt awaiting browser verification cannot become ready through polling.

Missing tab context, a changed login identity, or an expired login fails the interactive flow without guessing identity or forwarding the session through login URLs. `session_uri`, redirect tokens, nonce values, external references, and account credentials are excluded from access logs, request telemetry, and audit details. Console uses no third-party callback resources and a no-referrer policy; the ingress also excludes verifier query strings from access logs. Public URLs derive from configured origin, never forwarded-host headers.

Interactive setup belongs to the Workspace and requires the initiating authenticated User and current management authority. A Service Account cannot initiate a browser flow. Callback and polling completion recheck the initiating User's current management authority and share the same connection, generation, attempt, Provider, Workspace, and expiry fences; disabling a connection prevents late completion from restoring readiness.

## ConnectorConnection Lifecycle

Adapters map only authoritative upstream facts into the finite Service statuses `pending`, `ready`, `action_required`, and `disabled`. A confirmed expired, revoked, or interaction-required account becomes `action_required` with `reauthorization_required`; an unknown state, provider mismatch, or unsupported compatibility profile uses `incompatible`. A timeout, cancellation, rate limit, or 5xx response is a transient observation and does not downgrade a ready ConnectorConnection.

Revocation and deletion commit local invalidation before one bounded remote attempt. A persisted command receipt records the actual local and remote outcomes. Unknown or failed cleanup is not retried in the background, and idempotent replay does not repeat the effect. Deletion retains identity evidence for accepted Runs and never claims an unconfirmed remote revocation succeeded.

## Connector Discovery

Registered adapters expose `discover_connectors` for one exact configured Connector Provider under the [Connector discovery contract](03-connectors-and-connections.md#connector-discovery). Composio maps toolkit identities. Each projection contains safe display metadata and only the non-secret setup options and hosted authentication methods that the configured account can use. Upstream authentication configuration secrets are never copied into a discovery result.

Provider type definitions come from trusted code and describe backend configuration; Connector discovery describes that backend account's available integrations. Neither is the per-Connection tool catalog. A discovered GitHub Connector under one Composio account cannot authorize setup or dispatch under a second account, even when both Providers have `type="composio"`.

## Tool Discovery Contract

Provider-level tool preview precedes external account authorization. A caller authorized to use the Provider credential selects a Connector/toolkit and reads its tool names, descriptions, parameter schemas, and upstream versions without creating or binding a ConnectorConnection. Preview is an advisory catalog observation; it neither proves account-specific tool availability nor grants execution authority. It performs no account setup, authorization, revocation, or tool execution. Connector discovery, tool preview, hosted account authorization, and account-bound execution remain separate operations.

Execution discovery binds one exact ConnectorConnection and its authorized ConnectorProvider, implementation profile, Connector/toolkit identity, and current credential generation, even if the upstream API offers project-wide tools. Preview and execution discovery share the same upstream parsing, version validation, and [discovery bounds](04-agent-facing-tools.md#discovery-and-result-bounds). After authorization, execution checks the account reference, Workspace correlation, readiness, and current bound tool definition; a preview cannot substitute for these checks. A changed or unauthorized binding invalidates the result rather than publishing it for another account.

Control uses discovery for setup checks and advisory management projections. The executing Worker uses it to populate the connection's in-process MCP tool group. Session caches are scoped to the exact authorized binding. There is no immutable catalog object, catalog digest in Run selections, or retained-Run catalog cleanup dependency. Provider versions needed for execution belong to the current discovered runtime binding.

## Composio v3.1

`type = "composio"` uses the fixed v3.1 REST API at `https://backend.composio.dev`. Provider configuration is an empty object; endpoint and protocol selection are implementation-owned. The ConnectorProvider owns write-only credential field `api_key`; deployments should use a scoped project key limited to required Connected Account read/write and tool read/execute operations.

All discoverable toolkits are available without a manually configured allowlist. The directory projects toolkit identity, description, logo, immutable toolkit version, managed OAuth availability, and enabled existing OAuth2 auth configurations. Unsupported authentication and missing version metadata produce an explicit unavailable reason. Third-party credentials and auth-config secret fields never enter the directory cache.

Managed OAuth is the default when supported. At connection setup, Service reads the current selected toolkit and configurations. It reuses an enabled managed OAuth2 configuration for that toolkit, or creates one using `POST /api/v3.1/auth_configs` with `use_composio_managed_auth`. Creation is coordinated by exact Provider and toolkit across processes. A durable claim precedes the POST, with no database transaction held during external I/O. A lost response or interrupted creator cannot cause another creation POST: a later setup first reconciles by finding the upstream managed configuration. If none is visible, setup reports `shared_setup_outcome_unknown`; the user resolves the configuration in Composio Dashboard before trying again. Credential replacement starts a new credential generation and invalidates the directory.

Existing enabled custom OAuth2 configurations are selectable by their names. Creation, client ID, client secret, scopes, and callback application settings belong to Composio Dashboard. Only safe ordinary connection fields from the selected toolkit's metadata can be prefilled by Service. A required unsupported or credential-bearing field makes this setup unavailable with an explanation; Service does not turn it into a generic secret form. No-auth toolkits and other authentication methods are identified as unavailable for this hosted account flow.

Hosted OAuth2 setup uses `POST /api/v3.1/connected_accounts/link` with the selected enabled auth configuration and opaque Workspace correlation. Its response contains `connected_account_id`, `redirect_url`, `expires_at`, and `link_token`; it does not contain `session_uri`. Redirects must use the exact HTTPS origin `https://connect.composio.dev`. The attempt retains the returned account reference and the earlier of its local expiry, ten minutes from creation, and upstream expiry. Service does not persist `link_token` or the authorization URL. Replaying a known Composio setup returns the same attempt without creating a link or recovering an unavailable URL.

The Composio project must configure the Console verifier URL. A link-specific `callback_url` does not replace project verifier configuration. After the authenticated browser returns, Service posts the single-use `session_uri` and exact expected external-user correlation to `POST /api/v3.1/connected_accounts/complete_auth`. Its success response contains `connected_account_id` and `toolkit_slug`; both must match the attempt. Service then reads `GET /api/v3.1/connected_accounts/{id}`, verifies `id`, `toolkit.slug`, and `user_id`, and accepts readiness only for an enabled `ACTIVE` account. Account credential fields are discarded.

Composio link creation has no assumed idempotency guarantee. An interrupted request with no retained account ID fails with unknown-outcome evidence and is never automatically resent. Explicit retry can create a new generation on an unbound Connection; old attempts cannot publish. A single-use callback session is never redeemed twice, including after a timeout or refusal. Uncertain redemption retains `reserved` and recovers only through exact-account GET until expiry. Initial and redemption work have bounded deadlines inside their lease duration; late owners cannot publish.

An already verified Composio Connection cannot reconnect through `/link`, which creates a new account. The deprecated upstream re-initiation API is not enabled by this adapter. Reconnect of a bound account returns `reconnect_unsupported`; the user must explicitly create a new Connection for another authorization. Safe inspection, revocation, and deletion continue to address the exact bound account.

Old Composio attempts without browser-binding evidence are failed by reconciliation and cannot be converted from an upstream session digest. Rollout stops old setup senders before enabling the new browser protocol; existing verified Connections retain their identity and readiness. The additive database migration retains the unused old digest column until a later contract migration.

The adapter does not use Composio Sessions or Tool Router as a Service Session, Run, or selection authority. It does not expose proxy execute. Catalog calls use `/api/v3.1/tools` and `/api/v3.1/tools/{tool_slug}` with an explicit dated toolkit version such as `YYYYMMDD_NN`; every manual execution uses `POST /api/v3.1/tools/execute/{tool_slug}` with that same version from the current discovery and the hidden connected-account binding. A fresh runtime resolves its current toolkit version before discovery rather than reusing a Run-owned version lock. Tool details are fetched with bounded concurrency, preserving directory order and one toolkit version within the discovery deadline. The v3.1 default of `latest` is never relied on for an individual execute call.

Complete versioned tool definitions returned by a directory page are used directly after checking their toolkit and version. Only sparse entries require individual detail requests; those requests retain bounded concurrency. A complete but inconsistent directory definition fails discovery rather than silently falling back to another definition.

For `GITHUB_GET_A_REPOSITORY` at Composio version `20260902_00`, the adapter corrects the known omission of nullable fields in the repository and license output definitions to match [GitHub's REST schemas](https://github.com/github/rest-api-description/blob/main/descriptions/api.github.com/api.github.com.json). This correction applies only to the named fields in that tool/version; it preserves original values, required fields, local references, and non-null constraints. Optional fields are not generally treated as nullable. Other tools and versions retain their source schemas unchanged.

Connected Account responses are accepted through a safe projection of ID, Workspace correlation, toolkit, finite status, timestamps, and non-sensitive display metadata. Credential, token, auth-config secret, state, and masked-secret fields are not part of the adapter response type and are discarded. Provider status values map explicitly; an unknown value is incompatible rather than ready.

## Failure and Retry Semantics

| Condition                                                                | Outcome                                                                                                         |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| ConnectorProvider endpoint or redirect violates outbound policy          | Fail before sending credentials or setup correlation                                                            |
| Setup response names another provider, Workspace correlation, or account | Fail closed and keep the intended ConnectorConnection unusable                                                  |
| external-service-hosted non-OAuth form is unavailable                    | Setup is incompatible; Service does not proxy the credential                                                    |
| Callback User, attempt, expiry, or returned account differs              | Reject callback without attaching the external account                                                          |
| Setup response is lost after possible dispatch                           | Preserve the same setup attempt; recover only through provider-supported idempotency, callback, or polling      |
| Upstream account needs interaction                                       | Set `action_required(reauthorization_required)` only on authoritative evidence                                  |
| Unknown upstream status or incompatible schema/profile                   | Set `action_required(incompatible)` and never guess readiness                                                   |
| Catalog exceeds a count, byte, page, or schema bound                     | Reject discovery explicitly; do not publish a partial or unauthorized tool group                                |
| Tool write response is lost                                              | Return `outcome_unknown` unless the ConnectorProvider supplies authoritative receipt or reconciliation evidence |

## Invariants

1. Service never receives or stores a third-party account credential behind a ConnectorConnection.
2. Provider directory values never grant access to an external account.
3. Registered Connector Provider setup, callback, inspection, catalog, revoke, and execution bind one immutable ConnectorConnection and one opaque external account reference.
4. Catalog refresh and external operations hold no database transaction open.
5. Composio execution pins its discovered upstream version.
6. No Connector Provider adapter exposes a public execute endpoint or becomes a second Agent Session, Run, or authorization system.

A shared-configuration creation claim survives credential rotation. Rotation invalidates the directory, but cannot prove that an earlier upstream POST was not accepted. Resolve uncertain creation by finding or configuring the OAuth app in the provider dashboard; an existing enabled configuration is always reconciled before the creation fence.
