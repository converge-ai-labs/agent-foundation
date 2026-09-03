# Built-in Connector Adapters

## Design Position

OpenConnector and Composio are independent built-in Connector adapters. They implement one common Foundation application boundary for account setup, safe inspection, revocation, validated catalog discovery, and bound tool execution, while preserving different upstream resources, statuses, endpoints, and compatibility profiles. Neither adapter becomes a Foundation Agent runtime or exposes an unfenced public execute route.

The Connector service owns every third-party account credential. Foundation stores only the credential used to call that Connector, an opaque external account reference, a safe account projection, immutable catalog objects, and bounded operation evidence. Responses are parsed through explicit safe allowlists; credential-shaped or undocumented fields are discarded before a value reaches logging, persistence, audit, or a public response.

## Common Setup and Correlation

Creating a ConnectorConnection commits the pending Foundation resource and a `csa_` setup attempt before calling a Connector. The expiring attempt binds its exact ConnectorConnection, generation, initiating Principal, intended owner, driver, provider key, opaque external-user correlation, and return target. External I/O runs without an open database transaction. A later short transaction attaches the verified external reference and safe redirect evidence or records a bounded retry observation.

External user correlation is a domain-separated HMAC under an operator secret over the exact Organization, Workspace, owner kind, owner ID, and Connector identity. It contains no email, display name, raw Foundation ID, or credential. A Connector response naming another correlation, provider, account, or setup generation fails closed. Once attached, the external account reference is immutable; repairing a setup never substitutes another account behind the same ConnectorConnection.

Foundation accepts only Connector-hosted authorization or credential forms. A provider API key, password, cookie, refresh token, or other third-party account credential must travel directly from the user's browser to the Connector. No Foundation management request or callback proxies such a value, even transiently.

The external callback is:

```http
GET /connectivity/v1/connector-setup/callback?session_uri=opaque
```

It is mounted by `control` and `all`, requires the current authenticated browser User, ignores forwarded-host data, and derives its public return URLs from configured public origin. The callback reserves one unexpired attempt for that exact User before external redemption and consumes it exactly once when redemption has an authoritative result. `session_uri`, Connector redirect tokens, external references, and account credentials are excluded from access logs, request telemetry, audit details, and redirect responses. An uncertain redemption leaves the same attempt reconcilable; it never creates a new ConnectorConnection.

Workspace-shared and User-personal interactive setup require this verified callback when the driver supports it. An Admin can initiate setup on behalf of a Workspace or Service Account, but the resulting ConnectorConnection owner remains the explicit Foundation owner and is never inferred from the browser or provider account. A Service Account cannot initiate an interactive browser flow itself.

## ConnectorConnection Lifecycle

Adapters map only authoritative upstream facts into the finite Foundation statuses `pending`, `ready`, `action_required`, and `disabled`. A confirmed expired, revoked, or interaction-required account becomes `action_required` with `reauthorization_required`; an unknown state, provider mismatch, or unsupported compatibility profile uses `incompatible`. A timeout, cancellation, rate limit, or 5xx response is a transient observation and does not downgrade a ready ConnectorConnection.

Revocation first makes the local resource ineligible and commits a stable operation generation. The external revoke/delete call occurs afterward. A confirmed external result completes the local transition; an unknown outcome is reconciled only by inspecting the same external reference. Deletion retains the tombstone and immutable compatibility evidence needed by retained Runs. It never claims that an unconfirmed external credential was revoked.

## Catalog Contract

Every validated catalog belongs to one exact ConnectorConnection, even when an upstream API exposes a project-wide tool list. Refresh claims the source and freezes the Connector, ConnectorConnection, credential generation, driver profile, provider/toolkit identity, and previous catalog before external I/O. It follows bounded pagination, validates every tool and JSON Schema, canonicalizes the complete catalog, stores one immutable `tcat_` object, and publishes it only after rechecking the frozen facts in a short transaction.

The common [catalog safety bounds](04-agent-facing-tools.md#catalog-safety-bounds) govern pagination, tool count, schema shape, canonical bytes, result bytes, and retention. This adapter adds the driver and API profiles plus provider/toolkit version to the catalog digest. The latest compatible catalog is selected for new Runs.

## OpenConnector native v1

`driver_key = "openconnector"` and `api_profile = "native_v1"` select the native `/api/v1` surface. Configuration identifies `deployment` as `cloud` or `self_hosted`, an exact normalized endpoint, and enabled provider slugs. Cloud and self-hosted installations are one driver with different endpoint policy, not different resource kinds. The Connector owns write-only Secret `api_key`, sent only as `x-api-key` to the configured origin.

OAuth setup calls `POST /api/v1/connectors/{slug}/initiate` with the selected auth configuration and opaque external-user correlation, and accepts only a bounded `connectionId` and Connector-hosted `redirectUrl`. Safe inspection, list, refresh, and deletion use the native connected-account routes for that exact ID. Direct server-side `/connect` is unsupported because its credential body would cross Foundation. A Connector without a hosted form for its required non-OAuth credential is incompatible with this profile.

Catalog discovery uses the native tool list plus exact tool-detail endpoints and pins the returned stable tool slug and schema under the ConnectorConnection. Execution uses `POST /api/v1/tools/{slug}/execute` with only the hidden bound `connectedAccountId` and validated model arguments. The adapter treats HTTP success as transport success only: the body must also report `successful = true` and a compatible nested status. Unknown lowercase status values are `incompatible`; casing is never normalized into a guessed known state.

OpenConnector retains provider OAuth callbacks and tokens in its own vault. Foundation exposes neither its external connection ID nor the upstream account credential to the model.

## Composio v3.1

`driver_key = "composio"`, `connected_accounts_profile = "v3_1"`, and `tools_profile = "v3_1"` select the current REST profiles at `https://backend.composio.dev` or one exact operator-allowed compatible origin. The Connector owns write-only Secret `api_key`; deployments should use a scoped project key limited to required Connected Account read/write and tool read/execute operations.

Hosted setup uses `POST /api/v3.1/connected_accounts/link` with the intended auth configuration and opaque external-user correlation. Safe inspection, refresh, status change, revocation, and deletion use only the `/api/v3.1/connected_accounts` resources for the returned account. The project must configure Foundation's callback as its identity verifier. After the browser returns, Foundation posts the single-use `session_uri` and exact expected external-user correlation to `POST /api/v3.1/connected_accounts/complete_auth`; the session is accepted for no more than ten minutes. Completion must return the intended connected-account ID and toolkit before the ConnectorConnection can become ready.

The adapter does not use Composio Sessions or Tool Router as a Foundation Session, Run, or selection authority. It does not expose proxy execute. Catalog calls use `/api/v3.1/tools` and `/api/v3.1/tools/{tool_slug}` with an explicit dated toolkit version such as `YYYYMMDD_NN`; every manual execution uses `POST /api/v3.1/tools/execute/{tool_slug}` with that same retained version and the hidden connected-account binding. The v3.1 default of `latest` is never relied on.

Connected Account responses are accepted through a safe projection of ID, owner correlation, toolkit, finite status, timestamps, and non-sensitive display metadata. Credential, token, auth-config secret, state, and masked-secret fields are not part of the adapter response type and are discarded. Provider status values map explicitly; an unknown value is incompatible rather than ready.

## Failure and Retry Semantics

| Condition                                                            | Outcome                                                                                                 |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Connector endpoint or redirect violates outbound policy              | Fail before sending credentials or setup correlation                                                    |
| Setup response names another provider, owner correlation, or account | Fail closed and keep the intended ConnectorConnection unusable                                          |
| Connector-hosted non-OAuth form is unavailable                       | Setup is incompatible; Foundation does not proxy the credential                                         |
| Callback User, attempt, expiry, or returned account differs          | Reject callback without attaching the external account                                                  |
| Setup or revoke response is lost after possible dispatch             | Preserve unknown outcome and reconcile the same attempt or external reference                           |
| Upstream account needs interaction                                   | Set `action_required(reauthorization_required)` only on authoritative evidence                          |
| Unknown upstream status or incompatible schema/profile               | Set `action_required(incompatible)` and never guess readiness                                           |
| Catalog exceeds a count, byte, page, or schema bound                 | Reject the candidate; retain the last compatible immutable catalog                                      |
| Tool write response is lost                                          | Return `outcome_unknown` unless the Connector supplies authoritative receipt or reconciliation evidence |

## Invariants

1. Foundation never receives or stores a third-party account credential behind a ConnectorConnection.
2. OpenConnector and Composio remain separate drivers and never impersonate one another through compatibility endpoints.
3. Setup, callback, inspection, catalog, revoke, and execution bind one immutable ConnectorConnection and one opaque external account reference.
4. Catalog refresh and external operations hold no database transaction open.
5. A retained Run uses an exact immutable catalog and provider-owned version; no execution resolves `latest`.
6. No Connector adapter exposes a public execute endpoint or becomes a second Agent Session, Run, or authorization system.
