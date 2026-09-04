# Built-in Connector Provider Adapters

## Design Position

Composio implements the registered Connector Provider boundary for Foundation account setup, safe inspection, revocation, catalog discovery, and bound tool execution. [OOMOL OpenConnector](https://github.com/oomol-lab/open-connector) supplies a separate personal/self-hosted runtime integration. Its credential scope and account selection are not a Foundation owner binding. Neither integration becomes a Foundation Agent runtime or exposes an unfenced public execute route.

The external integration service owns every third-party account credential. Foundation stores only the credential used to call that ConnectorProvider, an opaque external account reference, a safe account projection and bounded operation evidence. Responses are parsed through explicit safe allowlists; credential-shaped or undocumented fields are discarded before a value reaches logging, persistence, audit, or a public response.

## Common Setup and Correlation

Creating a ConnectorConnection commits the pending Foundation resource and a `csa_` setup attempt before calling a ConnectorProvider. The expiring attempt binds its exact ConnectorConnection, generation, initiating Principal, intended owner, Provider type, Connector key, opaque external-user correlation, and return target. External I/O runs without an open database transaction. A later short transaction attaches the verified external reference and safe redirect evidence or records a bounded retry observation.

External user correlation is a domain-separated HMAC under an operator secret over the exact Organization, Workspace, owner kind, owner ID, and ConnectorProvider identity. It contains no email, display name, raw Foundation ID, or credential. A ConnectorProvider response naming another correlation, provider, account, or setup generation fails closed. Once attached, the external account reference is immutable; repairing a setup never substitutes another account behind the same ConnectorConnection.

Foundation accepts only external-service-hosted authorization or credential forms. A provider API key, password, cookie, refresh token, or other third-party account credential must travel directly from the user's browser to the ConnectorProvider. No Foundation management request or callback proxies such a value, even transiently.

The external callback is:

```http
GET /connectivity/v1/connector-setup/callback?session_uri=opaque
```

It is mounted by `control` and `all`, requires the current authenticated browser User, ignores forwarded-host data, and derives its public return URLs from configured public origin. The callback reserves one unexpired attempt for that exact User before external redemption and consumes it exactly once when redemption has an authoritative result. `session_uri`, ConnectorProvider redirect tokens, external references, and account credentials are excluded from access logs, request telemetry, audit details, and redirect responses. An uncertain redemption leaves the same attempt reconcilable; it never creates a new ConnectorConnection.

Workspace-shared and User-personal interactive setup require this verified callback when the implementation supports it. An Admin can initiate setup on behalf of a Workspace or Service Account, but the resulting ConnectorConnection owner remains the explicit Foundation owner and is never inferred from the browser or provider account. A Service Account cannot initiate an interactive browser flow itself.

## ConnectorConnection Lifecycle

Adapters map only authoritative upstream facts into the finite Foundation statuses `pending`, `ready`, `action_required`, and `disabled`. A confirmed expired, revoked, or interaction-required account becomes `action_required` with `reauthorization_required`; an unknown state, provider mismatch, or unsupported compatibility profile uses `incompatible`. A timeout, cancellation, rate limit, or 5xx response is a transient observation and does not downgrade a ready ConnectorConnection.

Revocation first makes the local resource ineligible and commits a stable operation generation. The external revoke/delete call occurs afterward. A confirmed external result completes the local transition; an unknown outcome is reconciled only by inspecting the same external reference. Deletion retains the tombstone and immutable compatibility evidence needed by retained Runs. It never claims that an unconfirmed external credential was revoked.

## Connector Discovery

Registered adapters expose `discover_connectors` for one exact configured Connector Provider under the [Connector discovery contract](03-connectors-and-connections.md#connector-discovery). Composio maps toolkit identities. Each projection contains safe display metadata and only the non-secret setup options and hosted authentication methods that the configured account can use. Upstream authentication configuration secrets are never copied into a discovery result.

Provider type definitions come from trusted code and describe backend configuration; Connector discovery describes that backend account's available integrations. Neither is the per-Connection tool catalog. A discovered GitHub Connector under one Composio account cannot authorize setup or dispatch under a second account, even when both Providers have `type="composio"`.

## Tool Discovery Contract

Provider-level tool preview precedes external account authorization. A caller authorized to use the Provider credential selects a Connector/toolkit and reads its tool names, descriptions, parameter schemas, and upstream versions without creating or binding a ConnectorConnection. Preview is an advisory catalog observation; it neither proves account-specific tool availability nor grants execution authority. It performs no account setup, authorization, revocation, or tool execution. Connector discovery, tool preview, hosted account authorization, and account-bound execution remain separate operations.

Execution discovery binds one exact ConnectorConnection and its authorized ConnectorProvider, implementation profile, Connector/toolkit identity, and current credential generation, even if the upstream API offers project-wide tools. Preview and execution discovery share the same upstream parsing, version validation, and [discovery bounds](04-agent-facing-tools.md#discovery-and-result-bounds). After authorization, execution checks the account reference, owner correlation, readiness, and current bound tool definition; a preview cannot substitute for these checks. A changed or unauthorized binding invalidates the result rather than publishing it for another account.

Control uses discovery for setup checks and advisory management projections. The executing Worker or Runner uses it to populate the connection's in-process MCP tool group. Session caches are scoped to the exact authorized binding. There is no immutable catalog object, catalog digest in Run selections, or retained-Run catalog cleanup dependency. Provider versions needed for execution belong to the current discovered runtime binding.

## OOMOL OpenConnector runtime v1

OpenConnector means the `oomol-lab/open-connector` project. The `runtime_v1` profile uses the documented `/v1` runtime surface with a personal OOMOL API key at `https://connector.oomol.com`, or a runtime token at one explicitly configured self-hosted origin. Credentials are sent only as `Authorization: Bearer` to that origin. Cloud configuration fixes the hosted origin; self-hosted configuration selects a different origin subject to outbound endpoint policy. Profiles are explicit and are never inferred from key prefixes.

Provider discovery reads `GET /v1/providers`; Action discovery reads `GET /v1/actions?service={service}` and `GET /v1/actions/{actionId}`. Responses use a `success`, `data`, and optional metadata envelope. These endpoints return complete arrays rather than cursor pages. Count and byte bounds apply before accepting the whole result. Provider identities can begin with digits. Actions retain their exact `service.action` IDs and native JSON Schemas, including composed and non-object output schemas. Neither directory nor Action preview requires an authorized third-party account.

`GET /v1/apps` lists connections visible to the current credential. The safe runtime projection contains only connection ID, service, alias, default-selection flag, and status; credential summaries, personal user IDs, and upstream tokens are discarded. Runtime calls select a visible connection explicitly, require `active`, and verify its exact ID and alias/default mapping immediately before dispatch. `POST /v1/actions/{actionId}` sends `{input: ...}` with the selected `x-oo-connector-alias` when named. An unnamed connection is eligible only when it is the unique advertised default. The caller supplies a fresh idempotency key and execution is never automatically retried.

Actions have no immutable upstream version parameter. A process-local digest of the complete observed definition detects schema, scope, or metadata changes before dispatch. It is not an upstream version or a retained Run lock. The runtime rereads the selected Action and rejects changed definitions. Definition and alias checks cannot atomically prevent upstream changes between validation and dispatch. Lost, oversized, malformed, 5xx, or conflicting (HTTP 409) write responses remain conservatively unknown outcomes; a successful HTTP response alone is insufficient without a valid success envelope.

Personal and self-hosted account authorization and revocation belong to the external console. This runtime profile neither collects third-party credentials nor invokes administration APIs with a runtime token. OOMOL's project-key `/v1/saas` API is a distinct identity and lifecycle contract: personal/runtime credentials cannot impersonate a SaaS project or supply Foundation's opaque external-user correlation. The personal/runtime integration is therefore not registered as a Foundation Connector Provider, cannot create a ConnectorConnection, and cannot populate a Foundation connection-bound tool group. The common setup, callback, owner-correlation, and revocation contracts apply only to registered adapters that attest those facts.

The upstream [runtime API](https://github.com/oomol-lab/open-connector/blob/main/docs/runtime-api.md) owns the native contract; the [OOMOL SDK](https://github.com/oomol-lab/connector-sdk) distinguishes personal, self-hosted, and project clients.

## Composio v3.1

`type = "composio"`, `connected_accounts_profile = "v3_1"`, and `tools_profile = "v3_1"` select the current REST profiles at `https://backend.composio.dev` or one exact operator-allowed compatible origin. The ConnectorProvider owns write-only credential field `api_key`; deployments should use a scoped project key limited to required Connected Account read/write and tool read/execute operations.

Hosted setup uses `POST /api/v3.1/connected_accounts/link` with the intended auth configuration and opaque external-user correlation. Safe inspection, refresh, status change, revocation, and deletion use only the `/api/v3.1/connected_accounts` resources for the returned account. The project must configure Foundation's callback as its identity verifier. After the browser returns, Foundation posts the single-use `session_uri` and exact expected external-user correlation to `POST /api/v3.1/connected_accounts/complete_auth`; the session is accepted for no more than ten minutes. Completion must return the intended connected-account ID and toolkit before the ConnectorConnection can become ready.

The adapter does not use Composio Sessions or Tool Router as a Foundation Session, Run, or selection authority. It does not expose proxy execute. Catalog calls use `/api/v3.1/tools` and `/api/v3.1/tools/{tool_slug}` with an explicit dated toolkit version such as `YYYYMMDD_NN`; every manual execution uses `POST /api/v3.1/tools/execute/{tool_slug}` with that same version from the current discovery and the hidden connected-account binding. A fresh runtime resolves its current toolkit version before discovery rather than reusing a Run-owned version lock. The v3.1 default of `latest` is never relied on for an individual execute call.

Connected Account responses are accepted through a safe projection of ID, owner correlation, toolkit, finite status, timestamps, and non-sensitive display metadata. Credential, token, auth-config secret, state, and masked-secret fields are not part of the adapter response type and are discarded. Provider status values map explicitly; an unknown value is incompatible rather than ready.

## Failure and Retry Semantics

| Condition                                                            | Outcome                                                                                                         |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| ConnectorProvider endpoint or redirect violates outbound policy      | Fail before sending credentials or setup correlation                                                            |
| Setup response names another provider, owner correlation, or account | Fail closed and keep the intended ConnectorConnection unusable                                                  |
| external-service-hosted non-OAuth form is unavailable                | Setup is incompatible; Foundation does not proxy the credential                                                 |
| Callback User, attempt, expiry, or returned account differs          | Reject callback without attaching the external account                                                          |
| Setup or revoke response is lost after possible dispatch             | Preserve unknown outcome and reconcile the same attempt or external reference                                   |
| Upstream account needs interaction                                   | Set `action_required(reauthorization_required)` only on authoritative evidence                                  |
| Unknown upstream status or incompatible schema/profile               | Set `action_required(incompatible)` and never guess readiness                                                   |
| Catalog exceeds a count, byte, page, or schema bound                 | Reject discovery explicitly; do not publish a partial or unauthorized tool group                                |
| Tool write response is lost                                          | Return `outcome_unknown` unless the ConnectorProvider supplies authoritative receipt or reconciliation evidence |

## Invariants

1. Foundation never receives or stores a third-party account credential behind a ConnectorConnection.
2. OOMOL OpenConnector runtime credentials and Composio project credentials retain separate endpoints, protocols, and authority boundaries.
3. Registered Connector Provider setup, callback, inspection, catalog, revoke, and execution bind one immutable ConnectorConnection and one opaque external account reference; runtime-only APIs never fabricate that binding.
4. Catalog refresh and external operations hold no database transaction open.
5. Composio execution pins its discovered upstream version; OOMOL runtime execution checks a current definition digest without claiming an atomic upstream version pin.
6. No Connector Provider adapter exposes a public execute endpoint or becomes a second Agent Session, Run, or authorization system.
