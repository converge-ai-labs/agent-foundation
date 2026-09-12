# Built-in Connector Provider Adapters

## Design Position

Composio implements the registered Connector Provider boundary. It owns a write-only service credential, establishes Workspace-bound Connections, and contributes tools through Agent `connection_tools`, including `defer_loading`. It does not become a Service Agent runtime or expose an unfenced public execute route.

The external integration service owns every third-party account credential. Service stores only the credential used to call that ConnectorProvider, an opaque external account reference, a safe account projection and bounded operation evidence. Responses are parsed through explicit safe allowlists; credential-shaped or undocumented fields are discarded before a value reaches logging, persistence, audit, or a public response.

## Common setup and correlation

Connection creation is local. Explicit authorization creates a durable short-lived operation before upstream work. The [common Connection authorization contract](03-connectors-and-connections.md#authorization) owns application authority, browser handoff, receipt and verifier checks, and completion status. Browser traffic lands on the Service-owned `/connection-authorizations/browser` page; the application's registered return URL is independent of Composio's verifier configuration.

An attempt binds the exact Connection, attempt generation, initiating principal, Workspace, Provider, Connector key, and opaque external-user correlation. One fenced sender owns initial work. Only its current unexpired lease can publish an upstream reply. External setup, redemption, and inspection run without an open transaction. A known replay returns the same operation and saved browser launch; it never creates another account or redeems a verifier again. An interrupted upstream write is retried only when the Provider supplies an authoritative idempotency guarantee.

External-user correlation is a domain-separated HMAC under an operator secret over Organization, Workspace, and Connector Provider identity. It contains no email, display name, raw Service ID, application end-user identity, or credential. Each completion validates the exact expected correlation, toolkit, and account reference. A new authorization can replace a previous account only through a confirmed generation change; recovery cannot substitute an account for an existing accepted Run.

Hosted forms send account credentials directly to the Provider. Direct credential authorization accepts write-only account fields and transmits them through the selected adapter without persisting them. Catalogs, configuration, safe projections, command evidence, and logs exclude account secrets. The generic setup abstraction never interprets model arguments as credentials.

Browser methods require the common per-tab and application-backend proof before reservation. Background recovery cannot promote an unreserved hosted or OAuth attempt. A reserved uncertain completion can recover only by inspecting the exact known account until expiry. Cancellation, expiry, or a superseding generation prevents later publication; neither claims to reverse a possibly completed upstream action.

## Connection lifecycle

The [shared lifecycle](03-connectors-and-connections.md#connection) applies to every source. A Connector is ready only with a verified binding. Provider-reported revocation or required user interaction makes it action-required. Disabling denies local use. Explicit authorization can bind a replacement account under the same Connection ID; explicit revocation and deletion operate on the current binding with bounded cleanup receipts.

## Connector Discovery

Registered adapters expose `discover_connectors` for one exact configured Connector Provider under the [Connector discovery contract](03-connectors-and-connections.md#connector-discovery). Composio maps toolkit identities. Each projection contains safe display metadata and only the non-secret setup options and hosted authentication methods that the configured account can use. Upstream authentication configuration secrets are never copied into a discovery result.

Provider type definitions come from trusted code and describe backend configuration; Connector discovery describes that backend account's available integrations. Neither is the per-Connection tool catalog. A discovered GitHub Connector under one Composio account cannot authorize setup or dispatch under a second account, even when both Providers have `type="composio"`.

## Tool Discovery Contract

Provider-level tool preview precedes external account authorization. A caller authorized to use the Provider credential selects a Connector/toolkit and reads its tool names, descriptions, parameter schemas, and upstream versions without creating or binding a Connection. Preview is an advisory catalog observation; it neither proves account-specific tool availability nor grants execution authority. It performs no account setup, authorization, revocation, or tool execution. Connector discovery, tool preview, hosted account authorization, and account-bound execution remain separate operations.

Execution discovery binds one exact Connection and its authorized ConnectorProvider, implementation profile, Connector/toolkit identity, and current credential generation, even if the upstream API offers project-wide tools. Preview and execution discovery share the same upstream parsing, version validation, and [discovery bounds](04-agent-facing-tools.md#discovery-and-result-bounds). After authorization, execution checks the account reference, Workspace correlation, readiness, and current bound tool definition; a preview cannot substitute for these checks. A changed or unauthorized binding invalidates the result rather than publishing it for another account.

Control uses discovery for setup checks and advisory management projections. The executing Worker uses it to populate the connection's in-process MCP tool group. Session caches are scoped to the exact authorized binding. There is no immutable catalog object, catalog digest in Run selections, or retained-Run catalog cleanup dependency. Provider versions needed for execution belong to the current discovered runtime binding.

## Composio v3.1

`type = "composio"` uses the fixed v3.1 REST API at `https://backend.composio.dev`. Provider configuration is an empty object; endpoint and protocol selection are implementation-owned. The ConnectorProvider owns write-only credential field `api_key`; deployments should use a scoped project key limited to required Connected Account read/write and tool read/execute operations.

All discoverable toolkits are available without a manually configured allowlist. The directory projects toolkit identity, description, logo, immutable toolkit version, authentication methods, and enabled existing auth configurations. This adapter supports `OAUTH2`, `API_KEY`, `BEARER_TOKEN`, and `BASIC` through Composio's hosted flow. Toolkit detail responses may express supported methods through `auth_config_details[].mode` instead of `auth_schemes`. No-auth toolkits, unsupported methods, and missing version metadata produce explicit unavailable reasons. Third-party credentials and auth-config secret fields never enter the directory cache.

Every existing configuration choice names its exact auth config, method, OAuth management mode, and safely projected scopes. A single choice is preselected; multiple choices require an explicit selection. Service never chooses the first of several managed configurations. Creating another configuration, choosing custom OAuth client credentials, and changing application scopes belong to Composio Dashboard. Console explains the custom-app steps and can refresh configurations without discarding the selected source or connection name.

When no configuration exists for a supported method, Service offers managed OAuth if the toolkit supports it, or a credential-free API key, bearer, or basic auth config if the toolkit requires no application-level credentials. At setup it reads current metadata and configurations, then creates through `POST /api/v3.1/auth_configs`: OAuth uses `use_composio_managed_auth`; the other methods use `use_custom_auth`, the documented `authScheme`, and empty app credentials. Required application-level credentials must be configured in the Dashboard. Account-level credentials and any missing instance details are collected directly by the hosted page. API callers may optionally supply `connection_data` to prefill non-secret primitive instance fields, such as subdomain or region. Discovery describes allowed fields per selected authentication configuration through conditional setup schema constraints. Setup validates against fresh metadata for that exact method before any upstream write; unknown fields, secret fields, and unsupported value types are rejected. Upstream required fields remain optional locally because the hosted page collects omitted values. Direct credential authorization supplies separate write-only account fields validated against the selected method. Service uses `POST /api/v3.1/connected_accounts` with the exact auth config, Workspace correlation, and documented authentication state; it never stores those account credentials. Hosted forms remain available when the application wants Composio to collect them.

Creation is fenced by exact Provider, toolkit, and authentication method across processes. An enabled matching configuration is reconciled before acquiring the durable creation claim; multiple matches require explicit selection. A lost response never permits another creation POST. If no matching configuration is visible, setup reports `shared_setup_outcome_unknown`; the user resolves it in Composio Dashboard. Credential rotation invalidates the directory but retains uncertain creation claims.

Browser authorization for all four methods uses `POST /api/v3.1/connected_accounts/link` with the selected enabled auth configuration and opaque Workspace correlation. Reauthorization can create another upstream account under the same Connection ID. The response supplies the expected account ID, redirect URL, and expiry. Redirects use the exact HTTPS origin `https://connect.composio.dev`. The expiring authorization record encrypts the provider URL so an idempotent replay can return the same launch capability without creating another link.

Each attempt fixes its completion method from the selected provider configuration: polling, OAuth verifier, hosted browser confirmation, or direct credentials. Callback parameters cannot select another method. Browser completion requires the shared application and per-tab proof before reservation. Direct credentials require the authenticated initiating application and no browser handoff.

For OAuth, the Composio project must configure the Service browser handoff URL. A link-specific `callback_url` does not replace project verifier configuration. Service redeems the single-use `session_uri` and exact expected external-user correlation through `POST /api/v3.1/connected_accounts/complete_auth`. The returned `connected_account_id` and `toolkit_slug` must match the attempt. Service then reads the exact account and verifies `id`, `toolkit.slug`, and `user_id` before accepting an enabled `ACTIVE` account. A hosted-return token cannot complete an OAuth attempt.

API key, bearer, and basic hosted forms return to the Service browser handoff without an OAuth verifier session. The common browser binding, initiating principal, receipt, and backend verifier protect completion. The application confirms its customer intent before calling complete. Service inspects only the attempt's saved account and ignores browser-supplied account IDs or status. This does not prove who entered credentials into a forwarded hosted link. Composio can mark credentials ACTIVE before a tool exercises them, so readiness does not independently certify credential validity. Credential fields are discarded on account reads.

Composio link creation has no assumed idempotency guarantee. An interrupted request with no retained account ID fails with unknown-outcome evidence and is never automatically resent. Explicit retry can create a new generation on an unbound Connection; old attempts cannot publish. A single-use callback session is never redeemed twice, including after a timeout or refusal. Uncertain redemption retains `reserved` and recovers only through exact-account GET until expiry. Initial and redemption work have bounded deadlines inside their lease duration; late owners cannot publish.

A new authorization on a verified Connection can establish another account through `/link` or direct credential authorization. Commit replaces the bound account only after exact attempt verification and advances `authorization_generation`. Old Runs remain fenced to the previous generation. Checks, revocation, and deletion address the current exact binding; the adapter does not use the deprecated re-initiation API.

The pre-public schema stores the completion method directly and has no obsolete upstream session-digest column or legacy callback path. Console, Service, and generated clients use the same completion contract.

The adapter does not use Composio Sessions or Tool Router as a Service Session, Run, or selection authority. It does not expose proxy execute. Catalog calls use `/api/v3.1/tools` and `/api/v3.1/tools/{tool_slug}` with an explicit dated toolkit version such as `YYYYMMDD_NN`; every manual execution uses `POST /api/v3.1/tools/execute/{tool_slug}` with that same version from the current discovery and the hidden connected-account binding. A fresh runtime resolves its current toolkit version before discovery rather than reusing a Run-owned version lock. Tool details are fetched with bounded concurrency, preserving directory order and one toolkit version within the discovery deadline. The v3.1 default of `latest` is never relied on for an individual execute call.

Complete versioned tool definitions returned by a directory page are used directly after checking their toolkit and version. Only sparse entries require individual detail requests; those requests retain bounded concurrency. A complete but inconsistent directory definition fails discovery rather than silently falling back to another definition.

For `GITHUB_GET_A_REPOSITORY` at Composio version `20260902_00`, the adapter corrects the known omission of nullable fields in the repository and license output definitions to match [GitHub's REST schemas](https://github.com/github/rest-api-description/blob/main/descriptions/api.github.com/api.github.com.json). This correction applies only to the named fields in that tool/version; it preserves original values, required fields, local references, and non-null constraints. Optional fields are not generally treated as nullable. Other tools and versions retain their source schemas unchanged.

Connected Account responses are accepted through a safe projection of ID, Workspace correlation, toolkit, finite status, timestamps, and non-sensitive display metadata. Credential, token, auth-config secret, state, and masked-secret fields are not part of the adapter response type and are discarded. Provider status values map explicitly; an unknown value is incompatible rather than ready.

## Failure and Retry Semantics

| Condition                                                                | Outcome                                                                                                         |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| ConnectorProvider endpoint or redirect violates outbound policy          | Fail before sending credentials or setup correlation                                                            |
| Setup response names another provider, Workspace correlation, or account | Fail closed and keep the intended Connection unusable                                                           |
| external-service-hosted non-OAuth form is unavailable                    | Setup is incompatible; Service does not proxy the credential                                                    |
| Callback User, attempt, expiry, or returned account differs              | Reject callback without attaching the external account                                                          |
| Setup response is lost after possible dispatch                           | Preserve the same setup attempt; recover only through provider-supported idempotency, callback, or polling      |
| Upstream account needs interaction                                       | Set `action_required(reauthorization_required)` only on authoritative evidence                                  |
| Unknown upstream status or incompatible schema/profile                   | Set `action_required(incompatible)` and never guess readiness                                                   |
| Catalog exceeds a count, byte, page, or schema bound                     | Reject discovery explicitly; do not publish a partial or unauthorized tool group                                |
| Tool write response is lost                                              | Return `outcome_unknown` unless the ConnectorProvider supplies authoritative receipt or reconciliation evidence |

## Invariants

1. Service never stores third-party account credentials behind a Connector source; direct setup accepts them only as write-only input.
2. Provider directory values never grant access to an external account.
3. Registered Connector Provider setup, callback, inspection, catalog, revoke, and execution bind one Connection and the exact authorization-generation account reference.
4. Catalog refresh and external operations hold no database transaction open.
5. Composio execution pins its discovered upstream version.
6. No Connector Provider adapter exposes a public execute endpoint or becomes a second Agent Session, Run, or authorization system.

A shared-configuration creation claim survives credential rotation. Rotation invalidates the directory, but cannot prove that an earlier upstream POST was not accepted. Resolve uncertain creation by finding or configuring the auth config in the provider dashboard; an existing enabled configuration is always reconciled before the creation fence.
