# Built-in Connector Provider Adapters

## Design Position

Composio implements the registered Connector Provider boundary. It owns a write-only service credential, establishes Workspace-bound ConnectorConnections, and contributes tools through Agent `connector_tools`, including `defer_loading`. It does not become a Service Agent runtime or expose an unfenced public execute route.

The external integration service owns every third-party account credential. Service stores only the credential used to call that ConnectorProvider, an opaque external account reference, a safe account projection and bounded operation evidence. Responses are parsed through explicit safe allowlists; credential-shaped or undocumented fields are discarded before a value reaches logging, persistence, audit, or a public response.

## Common Setup and Correlation

Creating a ConnectorConnection commits the pending Service resource and a `csa_` setup attempt before calling a ConnectorProvider. The expiring attempt binds its exact ConnectorConnection, generation, initiating Principal, Workspace, Provider type, Connector key, opaque external-user correlation, and return target. External I/O runs without an open database transaction. A short transaction records the temporary upstream setup reference and safe redirect evidence. Authoritative completion publishes the complete verified account binding on the Connection atomically. Setup history is not runtime identity.

Initial authorization has one durable sender shared by management requests and background recovery. The initial command inserts the attempt as `starting`, owns its sender lease, and records idempotency and audit evidence in the same transaction. After commit the sender calls the Provider without claiming again. Recovery acquires a new fenced lease in a separate transaction. A concurrent management replay returns the same pending attempt without a redirect while its sender is active. A different command key cannot start the same generation again: it returns `setup_already_started` without external work. An explicit reconnect starts a new generation when supported. Cancellation or lease expiry does not make a request safe to repeat: an interrupted initial request can be retried only when the Provider guarantees idempotent setup; otherwise the attempt fails with unknown-outcome evidence. A known retryable refusal can return to pending after backoff. Once a setup reference is retained, management replay returns the saved attempt without dispatching setup again. Only the current unexpired sender lease may publish the initial request's result or failure.

External user correlation is a domain-separated HMAC under an operator secret over the exact Organization, Workspace, Workspace scope and ConnectorProvider identity. It contains no email, display name, raw Service ID, or credential. A ConnectorProvider response naming another correlation, provider, account, or setup generation fails closed. Only completed setup publishes the external account reference on the Connection. Before completion it belongs to the attempt. Once published, the reference is immutable; repairing a setup never substitutes another account behind the same ConnectorConnection.

Service accepts only external-service-hosted authorization or credential forms. A provider API key, password, cookie, refresh token, or other third-party account credential must travel directly from the user's browser to the ConnectorProvider. No Service management request or callback proxies such a value, even transiently.

The Composio OAuth verifier lands on a same-origin Console page:

```http
GET /connector-setup/callback?session_uri=opaque
```

Console owns this page; Service does not serve browser assets. Before setup, the authorization tab generates a 32-byte random nonce, submits its 64-character lowercase hex encoding as `browser_nonce`, and retains it in `sessionStorage`. Service stores only its SHA-256 digest on the attempt; the nonce also participates in the command fingerprint through its digest. After setup returns, the tab binds the returned `attempt_id` and expiry to that nonce. Composio receives neither the nonce nor browser state. The authorization link opens in that same tab. One tab owns one current authorization; the Service never selects the latest attempt for a user or Workspace.

The page removes the entire callback query from the address bar before starting authentication requests, retains only the OAuth session in memory, and sends the following authenticated mutation with the normal Origin and CSRF proof:

```http
POST /api/v1/connector-setup/complete
Content-Type: application/json

{"attempt_id":"csa_...","browser_nonce":"<64 lowercase hex characters>","session_uri":"opaque"}
```

For non-OAuth forms, the same endpoint receives the attempt and browser nonce without `session_uri`, only after explicit user confirmation as described below.

Control and all roles mount the completion API. It looks up the exact attempt and validates the browser digest, initiating User, current management authority, Workspace, Provider, generation, and expiry before reserving one redemption lease. External redemption and exact-account inspection happen after commit. A second fenced transaction revalidates eligibility and publishes the verified binding and `ready` together with terminal attempt and audit evidence. Successful completion returns the server-retained `return_path`. Setup accepts only an absolute local path beginning with one slash; network-path references beginning with two slashes are rejected. A completed replay returns that path only for the same authorized browser binding and current ready generation. A concurrent or uncertain redemption stays `reserved`; submitting it again never repeats redemption. Background recovery can inspect only the known account after browser verification or confirmation was reserved. An `attached` attempt awaiting browser verification or confirmation cannot become ready through polling.

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

All discoverable toolkits are available without a manually configured allowlist. The directory projects toolkit identity, description, logo, immutable toolkit version, authentication methods, and enabled existing auth configurations. This adapter supports `OAUTH2`, `API_KEY`, `BEARER_TOKEN`, and `BASIC` through Composio's hosted flow. Toolkit detail responses may express supported methods through `auth_config_details[].mode` instead of `auth_schemes`. No-auth toolkits, unsupported methods, and missing version metadata produce explicit unavailable reasons. Third-party credentials and auth-config secret fields never enter the directory cache.

Every existing configuration choice names its exact auth config, method, OAuth management mode, and safely projected scopes. A single choice is preselected; multiple choices require an explicit selection. Service never chooses the first of several managed configurations. Creating another configuration, choosing custom OAuth client credentials, and changing application scopes belong to Composio Dashboard. Console explains the custom-app steps and can refresh configurations without discarding the selected source or connection name.

When no configuration exists for a supported method, Service offers managed OAuth if the toolkit supports it, or a credential-free API key, bearer, or basic auth config if the toolkit requires no application-level credentials. At setup it reads current metadata and configurations, then creates through `POST /api/v3.1/auth_configs`: OAuth uses `use_composio_managed_auth`; the other methods use `use_custom_auth`, the documented `authScheme`, and empty app credentials. Required application-level credentials must be configured in the Dashboard. Account-level credentials, scopes requested by the hosted ceremony, and instance details such as subdomain or region are collected directly by the hosted page; Service has no local `connection_data` form or credential proxy.

Creation is fenced by exact Provider, toolkit, and authentication method across processes. An enabled matching configuration is reconciled before acquiring the durable creation claim; multiple matches require explicit selection. A lost response never permits another creation POST. If no matching configuration is visible, setup reports `shared_setup_outcome_unknown`; the user resolves it in Composio Dashboard. Credential rotation invalidates the directory but retains uncertain creation claims.

All four methods use `POST /api/v3.1/connected_accounts/link` with the selected enabled auth configuration and opaque Workspace correlation. Each explicit new Connection can authorize another account on the same config. The response supplies `connected_account_id`, `redirect_url`, and `expires_at`. Redirects must use the exact HTTPS origin `https://connect.composio.dev`. The attempt retains the account reference and the earlier of its local expiry, ten minutes from creation, and upstream expiry. Service does not persist the link token or authorization URL. A known setup replay returns the same attempt without creating or recovering a link.

The attempt records one completion method: `polling`, `oauth_verifier`, or `browser_confirmation`. The adapter selects it from the resolved upstream auth config, never from callback parameters. Both Composio methods require the original authenticated User, tab nonce, current authority, and attempt fences before reservation; neither can become ready by polling an unreserved attempt.

For OAuth, the Composio project must configure the Console verifier URL. A link-specific `callback_url` does not replace project verifier configuration. Service redeems the single-use `session_uri` and exact expected external-user correlation through `POST /api/v3.1/connected_accounts/complete_auth`. The returned `connected_account_id` and `toolkit_slug` must match the attempt. Service then reads the exact account and verifies `id`, `toolkit.slug`, and `user_id` before accepting an enabled `ACTIVE` account. A hosted-return token cannot complete an OAuth attempt.

API key, bearer, and basic hosted forms return the supplied callback URL without an OAuth verifier session. Console requires explicit confirmation in the original authenticated tab, names the connection and workspace, and explains that Composio cannot verify which browser submitted these credentials. Service requires the initiating User and tab nonce, reserves completion, and inspects only the attempt's saved account. Browser-supplied account IDs and status are ignored. This confirms the initiating user's intent; it does not prove who entered the credentials and does not prevent an initiator from forwarding a hosted link to another person and then confirming their submission. Callback URLs are visible before credential entry and cannot serve as completion proofs. OAuth attempts cannot use this confirmation path. Composio may store a credential as `ACTIVE` before a tool has exercised it; readiness does not independently certify credential validity. Account credential fields are discarded on every read.

Composio link creation has no assumed idempotency guarantee. An interrupted request with no retained account ID fails with unknown-outcome evidence and is never automatically resent. Explicit retry can create a new generation on an unbound Connection; old attempts cannot publish. A single-use callback session is never redeemed twice, including after a timeout or refusal. Uncertain redemption retains `reserved` and recovers only through exact-account GET until expiry. Initial and redemption work have bounded deadlines inside their lease duration; late owners cannot publish.

An already verified Composio Connection cannot reconnect through `/link`, which creates a new account. The deprecated upstream re-initiation API is not enabled by this adapter. Reconnect of a bound account returns `reconnect_unsupported`; the user must explicitly create a new Connection for another authorization. Safe inspection, revocation, and deletion continue to address the exact bound account.

The pre-public schema stores the completion method directly and has no obsolete upstream session-digest column or legacy callback path. Console, Service, and generated clients use the same completion contract.

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

A shared-configuration creation claim survives credential rotation. Rotation invalidates the directory, but cannot prove that an earlier upstream POST was not accepted. Resolve uncertain creation by finding or configuring the auth config in the provider dashboard; an existing enabled configuration is always reconciled before the creation fence.
