# Built-in Connector Provider Adapters

## Design Position

Composio and [OOMOL OpenConnector](https://github.com/oomol-lab/open-connector) implement the same registered Connector Provider boundary. Both own write-only service credentials, establish Workspace-bound ConnectorConnections, and contribute tools through Agent `connector_tools`, including `defer_loading`. Their upstream API capabilities remain distinct. OpenConnector uses the OOMOL project API for authorization initiated through Service and execution bound to the resulting ConnectorConnection. Neither integration becomes a Service Agent runtime or exposes an unfenced public execute route.

The external integration service owns every third-party account credential. Service stores only the credential used to call that ConnectorProvider, an opaque external account reference, a safe account projection and bounded operation evidence. Responses are parsed through explicit safe allowlists; credential-shaped or undocumented fields are discarded before a value reaches logging, persistence, audit, or a public response.

## Common Setup and Correlation

Creating a ConnectorConnection commits the pending Service resource and a `csa_` setup attempt before calling a ConnectorProvider. The expiring attempt binds its exact ConnectorConnection, generation, initiating Principal, Workspace, Provider type, Connector key, opaque external-user correlation, and return target. External I/O runs without an open database transaction. A short transaction records the temporary upstream setup reference and safe redirect evidence. Authoritative completion publishes the complete verified account binding on the Connection atomically. Setup history is not runtime identity.

Initial authorization has one durable sender shared by management requests and background recovery. The initial command inserts the attempt as `starting`, owns its sender lease, and records idempotency and audit evidence in the same transaction. After commit the sender calls the Provider without claiming again. Recovery acquires a new fenced lease in a separate transaction. A concurrent management replay returns the same pending attempt without a redirect while its sender is active. Cancellation or lease expiry does not make a request safe to repeat: an interrupted initial request can be retried only when the Provider guarantees idempotent setup; otherwise the attempt fails with unknown-outcome evidence. A known retryable refusal can return to pending after backoff. Once a setup reference is retained, replay resumes that exact reference. Only the current unexpired sender lease may publish the initial request's result or failure.

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

Control and all roles mount the completion API. It looks up the exact attempt and validates the browser digest, initiating User, current management authority, Workspace, Provider, generation, and expiry before reserving one redemption lease. External redemption and exact-account inspection happen after commit. A second fenced transaction revalidates eligibility and publishes the verified binding and `ready` together with terminal attempt and audit evidence. Successful completion returns the server-retained `return_path`. A completed replay returns that path only for the same authorized browser binding and current ready generation. A concurrent or uncertain redemption stays `reserved`; submitting it again never repeats redemption. Background recovery can inspect only the known account after browser verification was reserved. An `attached` attempt awaiting browser verification cannot become ready through polling.

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

## OOMOL OpenConnector managed project v1

`type = "openconnector"` registers the managed integration alongside Composio. Configuration selects `enabled_services` at `https://connector.oomol.com`. The encrypted Provider credential bundle has two write-only service credentials: `project_api_key` for `/v1/saas`, and `catalog_api_key` for the read-only `/v1` Provider and Action catalog. Neither is a third-party account credential. A credential test verifies catalog reads and reports `verified_access=["catalog_read"]`; the published Project API has no credential introspection operation, so project authority is verified during setup and exact-account profile inspection.

Catalog discovery reads `GET /v1/providers` and `GET /v1/actions?service={service}` with Bearer authentication. Both return complete arrays inside a `success` and `data` envelope; count and byte bounds apply to the entire result. Actions retain their exact `service.action` IDs and native JSON Schemas, including composed output schemas. Catalog access performs no account lookup or tool execution.

OAuth setup posts `{userId, service, alias}` to `/v1/saas/connected-accounts/link`. `userId` is Service's opaque Workspace correlation; `alias` is the setup attempt ID, never a model-selected account. The returned request ID belongs to the expiring setup attempt. The browser visits the returned HTTPS authorization URL; Control polls `/v1/saas/connection-requests/{request_id}`. A completed request must attest the same service and external-user correlation. Service verifies the resulting `connectedAccountId` through `/v1/saas/connected-accounts/{account_id}/profile` before publishing readiness. OpenConnector does not use Service's Composio callback route or require a public origin for polling. Service does not expose the SDK's API-key/custom-credential setup methods because they would route third-party secrets through Service.

Runtime identity contains the verified connected-account ID and Workspace correlation, independently of the request record. Before dispatch, the adapter verifies the exact account through its live profile and rereads current Action definitions. Execution posts `{userId, service, connectedAccountId, input}` to `/v1/saas/actions/{action_id}`. It always supplies the exact account ID; personal aliases, implicit defaults, and the user's latest active account cannot select a replacement. The local catalog digest detects changed definitions but is not an upstream immutable version. A failed profile read blocks dispatch without inventing a new account status from an ambiguous error. Malformed request or profile responses become controlled Provider errors; they fail the affected setup without stopping reconciliation for other connections.

The published Project API promises neither idempotent link creation nor an account-revocation operation. A lost setup response, cancellation after possible dispatch, or an interrupted sender recovered after lease expiry leaves a failed attempt with unknown-outcome evidence and is not automatically replayed. After a request reference is known, repeating the setup management command reads that existing request and its authorization URL rather than creating another link; safe polling can recover completion. Tool writes are never automatically retried; lost or invalid results remain `outcome_unknown`. Local revoke/delete always fences the connection first; unsupported remote revocation produces a failed cleanup receipt, and remote administration remains in OOMOL. Reconnect must preserve the verified account identity; if upstream creates another account, Service rejects substitution and the user must create a new Connection explicitly.

## Composio v3.1

`type = "composio"`, `connected_accounts_profile = "v3_1"`, and `tools_profile = "v3_1"` select the current REST profiles at `https://backend.composio.dev` or one exact operator-allowed compatible origin. The ConnectorProvider owns write-only credential field `api_key`; deployments should use a scoped project key limited to required Connected Account read/write and tool read/execute operations.

Hosted OAuth2 setup uses `POST /api/v3.1/connected_accounts/link` with the selected enabled auth configuration and opaque Workspace correlation. Its response contains `connected_account_id`, `redirect_url`, `expires_at`, and `link_token`; it does not contain `session_uri`. Redirects must use the exact HTTPS origin `https://connect.composio.dev`. The attempt retains the returned account reference and the earlier of its local expiry, ten minutes from creation, and upstream expiry. Service does not persist `link_token` or the authorization URL. Replaying a known Composio setup returns the same attempt without creating a link or recovering an unavailable URL.

The Composio project must configure the Console verifier URL. A link-specific `callback_url` does not replace project verifier configuration. After the authenticated browser returns, Service posts the single-use `session_uri` and exact expected external-user correlation to `POST /api/v3.1/connected_accounts/complete_auth`. Its success response contains `connected_account_id` and `toolkit_slug`; both must match the attempt. Service then reads `GET /api/v3.1/connected_accounts/{id}`, verifies `id`, `toolkit.slug`, and `user_id`, and accepts readiness only for an enabled `ACTIVE` account. Account credential fields are discarded.

Composio link creation has no assumed idempotency guarantee. An interrupted request with no retained account ID fails with unknown-outcome evidence and is never automatically resent. Explicit retry can create a new generation on an unbound Connection; old attempts cannot publish. A single-use callback session is never redeemed twice, including after a timeout or refusal. Uncertain redemption retains `reserved` and recovers only through exact-account GET until expiry. Initial and redemption work have bounded deadlines inside their lease duration; late owners cannot publish.

An already verified Composio Connection cannot reconnect through `/link`, which creates a new account. The deprecated upstream re-initiation API is not enabled by this adapter. Reconnect of a bound account returns `reconnect_unsupported`; the user must explicitly create a new Connection for another authorization. Safe inspection, revocation, and deletion continue to address the exact bound account.

Old Composio attempts without browser-binding evidence are failed by reconciliation and cannot be converted from an upstream session digest. Rollout stops old setup senders before enabling the new browser protocol; existing verified Connections retain their identity and readiness. The additive database migration retains the unused old digest column until a later contract migration.

The adapter does not use Composio Sessions or Tool Router as a Service Session, Run, or selection authority. It does not expose proxy execute. Catalog calls use `/api/v3.1/tools` and `/api/v3.1/tools/{tool_slug}` with an explicit dated toolkit version such as `YYYYMMDD_NN`; every manual execution uses `POST /api/v3.1/tools/execute/{tool_slug}` with that same version from the current discovery and the hidden connected-account binding. A fresh runtime resolves its current toolkit version before discovery rather than reusing a Run-owned version lock. Tool details are fetched with bounded concurrency, preserving directory order and one toolkit version within the discovery deadline. The v3.1 default of `latest` is never relied on for an individual execute call.

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
2. Service uses OpenConnector catalog credentials only for read-only Provider and Action discovery. Project credentials authorize managed setup, profile inspection, and exact-account execution.
3. Registered Connector Provider setup, callback, inspection, catalog, revoke, and execution bind one immutable ConnectorConnection and one opaque external account reference.
4. Catalog refresh and external operations hold no database transaction open.
5. Composio execution pins its discovered upstream version; OOMOL managed execution checks a current definition digest without claiming an atomic upstream version pin.
6. No Connector Provider adapter exposes a public execute endpoint or becomes a second Agent Session, Run, or authorization system.
