# Application Accounts

## Design Position

An Application Account is one concrete external identity operated directly by Service in one Workspace. It can be a provider user account, Bot, or application installation. It owns provider identity, API configuration, credentials, and administrative availability independently of event reception. In the Connectivity account namespace its type is `Account`; the product label is Application Account (应用账号).

## Boundaries

| Concern                                                                      | Owner               |
| ---------------------------------------------------------------------------- | ------------------- |
| External identity, API configuration, credentials, availability              | Application Account |
| Reception, default Agent, execution Service Account and default input policy | Application Account |
| Exact provider object Agent and narrow configuration override                | AccountTarget       |

A reusable provider application definition is not an Account. Each concrete provider-tenant installation or authorization is a separate Account. Provider adapters own exact identity meaning and credential schemas. ConnectorProvider, Connection retain their independent contracts.

## Account Resource

The following is a conceptual schema, not an ORM model:

```python
class Account:
    id: AccountId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    name: str
    provider_key: str
    provider_config_version: str
    provider_config: AccountProviderConfig
    status: Literal["active", "disabled"]
    receive_enabled: bool = False
    reception_scope: Literal["all_accessible", "configured_targets"] = "all_accessible"
    default_agent_id: AgentId | None
    execution_service_account_id: ServiceAccountId | None
    input_batching: InputBatchingPolicy | None
    provider_policy: ProviderPolicy | None
    version: int
    credential_configured: bool
    credential_generation: int
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

Account IDs use the `acct_` object prefix. Provider identity, Organization, and Workspace are immutable. Name and identity-preserving settings are mutable under exact version preconditions. Changing provider, installation, provider tenant, or account creates a different Account. Among non-deleted Accounts within a Workspace the provider-declared concrete identity is unique; mutable display names and transport configuration never determine that identity.

An Account belongs to its Workspace and can serve multiple Agents. `receive_enabled` defaults to false. `default_agent_id` and `execution_service_account_id` are optional for tool-only Accounts and required when reception is enabled. The execution identity is an active same-Workspace Service Account; external actors never supply Service authority. Optional `input_batching` and `provider_policy` provide reception defaults, with exact AccountTarget overrides defined by [event routing](01-ingress-and-routing.md#exact-account-targets).

An Account owns encrypted credential material under the shared [credential protection contract](../27-secret-management.md). API credentials and verification secrets are write-only owning-resource inputs. Rotation preserves Account identity and Thread correlation; no credential enters Agent input or public Secret selections.

## Reception and Lifecycle

The versioned `reception_scope` selects `all_accessible` (legacy default) or `configured_targets`. In configured-target mode, an event without an exact enabled target is acknowledged as irrelevant before durable input admission. Disabled targets never fall through. New Bot wizard Accounts explicitly use configured-target mode; existing Accounts and clients omitting the field retain all-accessible behavior. Changing this setting affects new admission, not already acknowledged batches.

Account creation starts no Agent. Reception is embedded Account configuration, with no separate Ingress identity or Agent allowlist. Supported transport settings and credentials use the provider-owned Account schema.

Account defaults select input execution authority and the default Agent. Exact targets select an optional Agent and narrow override. Changing defaults preserves `(account_id, external_ref.kind, external_ref.id)` Thread correlation. Each ordinary Run uses current configuration; an active or selected waiting Run receives only Steer.

`active` is administrative eligibility, not a health claim. A disabled Account blocks reception and subsequent provider dispatch, including from accepted Runs. Re-enabling it does not change `receive_enabled`.

Setting `receive_enabled=false` on an Account or exact target stops new admission; already acknowledged batches continue processing. Accepted replies retain their protected target and action policy and continue checking Account availability, execution Principal, and Attempt fencing. Reception settings do not grant proactive authority.

Account deletion makes the identity unavailable and clears its credentials. Retained Run, Event, Batch, and Binding evidence keeps its original identity; recovery never substitutes another Account. A newly created Account inherits no retained Run authority.

## Default Tools and Authority

Application Account tools are default host-injected capabilities. They do not belong to Agent configuration, Revisions, overrides, or user-selected connection lists. The trusted Run entry authorizes the Account, exact actions, and provider-typed target scope and freezes them as protected Run context. Inbound admission supplies current-conversation authority; an independent trusted entry can authorize proactive operations without reception. No context means no Account tools, and Service never enumerates all Workspace Accounts to derive defaults.

Account use and target authority are distinct. Binding an Account requires current `application_account.use` authority. An allowed operation does not authorize targets outside the entry's scope. The entry must establish target authority from its trusted policy; validating a target's shape is not permission to use it. Proactive operations expose provider-native destination arguments only within that scope. Current-conversation reply operations use the admitted target and expose no destination selector. Inbound reply authority does not enable proactive send tools.

The [Agent-facing tools contract](04-agent-facing-tools.md#default-native-tool-contexts) owns protected contexts, runtime composition, and continuation behavior. An Account may contribute proactive actions and a narrower inbound reply surface to the same Run; each retains its own scope. Credentials resolve at execution time. Tool arguments cannot choose another Account, credential, provider tenant, or API origin.

Workspace Admin manages Accounts and credentials. Viewer has safe metadata read access; Runner, Builder, and Admin use Accounts only through authorized Run contexts. Trusted entry binding checks current Workspace authority; dispatch evaluates permissions against the [Attempt IAM snapshot](../33-identity-and-access-management.md#attempt-iam-snapshot) and checks current resource eligibility and Attempt fencing. An external sender is never a Service Principal.

## Built-in Proactive Scopes

All target collections contain at most 128 entries; an empty collection authorizes no targets. Target scopes reject unknown fields. Account tool names are exact action allowlists, with no wildcard or all-tools mode.

| Provider | Tools                                                                                           | Target scope                                                                   |
| -------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Slack    | `slack.send_message`                                                                            | `channel_ids`: exact channel IDs                                               |
| Lark     | `lark.send_message`                                                                             | `chat_ids`: exact chat IDs                                                     |
| GitHub   | `github.add_comment`, `github.read_comments`, `github.read_issue_or_pr`, `github.list_pr_files` | `repositories`: unique entries with `repository_id`, `owner`, and `repository` |

Slack and Lark send actions accept the selected destination ID and provider-specific message content. GitHub actions accept `repository_id`, positive issue/PR `number`, and `target_kind` (`issue` or `pull_request`), plus their bounded action content. The configured owner/name locates the API resource; the native client obtains an installation token restricted to the exact immutable repository ID before dispatching the target action. PR file listing requires a pull-request target. No action accepts a caller-selected API origin or credential.

## Management API

`GET /api/v1/workspaces/{workspace}/application-account-provider-types` lists only distribution-registered Account adapters and configuration versions. Each entry exposes the provider-owned configuration, write-only credential, and reception-policy JSON schemas plus supported target kinds. This authorized metadata read performs no external I/O, returns no configured identity or credential, and does not grant account management permission. The same typed provider models own form metadata and request validation.

Account management uses `/api/v1/workspaces/{workspace}/application-accounts` for create/list and `/api/v1/application-accounts/{account_id}` for get/update/delete. Credential replacement uses `PUT .../credentials`; administrative commands use `POST .../enable` and `POST .../disable`. Creation and commands follow shared idempotency rules; mutations require exact version preconditions. Responses contain safe metadata only.

Exact object management uses the Account `/targets` child collection. Workspace Builders can manage targets within their current Agent and capability authority. Account management and credentials require Workspace Admin. Possession of an Account or target ID grants no authority.

### Bot Pilot Activation

Bot setup, checks, discovery, and activation use the `bots` OpenAPI/SDK group under the Account's `/bot/` prefix. Account-owned memory directory and sharing routes use `bot-memory`; the existing Account and target resource operations retain `connectivity-management`. These operation groups do not add an independent Bot identity or credential store.

`POST .../bot/activate` is the explicit activation path for a single-conversation Bot pilot. The request supplies the expected Account version, exact target ID/version and conversation ID, default Agent, execution Service Account, and typed messaging policy. The Account must be active, reception disabled, and admission restricted to configured targets. Exactly one target is enabled, and that target inherits the Account's Agent, capabilities, and response policy. Existing broader routing or advanced target overrides are reviewed through canonical Account/target management rather than silently replaced by the wizard.

Service verifies the installation and current pilot membership/activity through bounded native provider reads outside SQL transactions. Discovery results and prior saved checks alone cannot enable reception. After the read, one short transaction revalidates Account authority/version/credential generation, target identity/version, and the single enabled target condition, then applies the canonical Account update with current Agent and execution Service Account authorization. A change during verification, unknown membership, inactive conversation, provider failure, or execution-authority failure leaves this operation unapplied. Activation returns the updated Account; it does not send a provider message, invoke an Agent, or prove a reply was delivered. An uncertain response is reconciled by reading current Account state; retrying the old version cannot apply a second change.

### Bot Collection Metadata

`GET /api/v1/workspaces/{workspace}/bots` projects Slack and Feishu Accounts under existing Account read authority. It returns Account metadata, the current credential generation's last verified external organization name, an external organization ID, configured conversation-target count, setup condition, and a minimal latest setup-test stage and observation time. An unverified organization ID may come from Account configuration; it does not constitute verified identity. Target count covers saved conversation targets, including disabled targets, rather than upstream membership.

Optional `platform`, `condition`, and literal `search` filters apply before bounded keyset pagination. Search covers the local Bot name and external organization name/ID. Cursors bind the requesting principal, Workspace, and filters. Deleted Accounts are absent. Listing reads stored metadata without contacting providers or requesting each Bot's details.

Setup conditions are `disabled`, `needs_verification`, `check_failed`, `reception_off`, and `receiving`. Administrative disablement takes precedence; otherwise current-generation installation verification precedes the reception setting. `receiving` means reception is configured and enabled, not that event delivery or replies have succeeded. Test stages distinguish waiting, expiry, changed configuration, event receipt, rejection, acceptance, and a provider-confirmed test reply. Account, credential, or exact target version changes make the test stale. A missing or deleted test target also makes it stale. The observation timestamps describe historical evidence, not a live health guarantee.

`GET /api/v1/application-accounts/{account_id}/bot/summary` returns the same metadata projection for one current messaging Account, under the same Account read authority. Bot overview and collection therefore share setup-condition and test-staleness semantics. Administrative disablement or disabled Account/target reception also makes a test stale.

The collection and single-Bot summary never return test message bodies, Run/Thread/Session references, or provider reply receipts. Reading detailed test, conversation, and reply evidence still requires the owning authority checks; collection metadata does not grant that access.

### Bot Setup Test Observations

`POST .../bot/tests` prepares a user-sent test for an exact enabled conversation target. It requires Account management authority, the current Account and target versions, and a canonical `Idempotency-Key`. The returned `btest_` identifier is the test marker. Preparation sends no platform message and accepts no Agent execution. The user sends a real Bot mention asking it to echo this marker; the fifteen-minute send deadline bounds initial correlation. Retrying preparation with the same key and request replays the original result, including after configuration changes. `GET .../bot/tests/latest` and `GET .../bot/tests/{test_id}` return current observations and staleness without sending or retrying work.

Test observations distinguish authenticated event receipt, canonical Run or Steer acceptance, and confirmed native reply. Only an unambiguous marker in normalized event text, received before the deadline under the exact Account credential/configuration and target generation, can establish receipt. An authenticated event ignored by response policy records the safe reason separately from receipt. Run/Steer references are retained atomically with input acceptance, independently of ingress replay retention. Admission rejection does not become execution success. A native reply is associated only when validated outgoing content contains the same unambiguous marker and its trusted Account, inbound binding, credential/configuration and live target generation match the accepted test. This also permits a delegated Run with the same protected inbound binding to provide the reply; its own Run/Attempt remain the observed sender. Markers never grant authority or select a reply destination.

The test stores references and observations, not a copy of incoming or outgoing message bodies. Inspection requires Account read authority and canonical Agent-scoped Session/Thread/Run read authority for any referenced execution and selected reply. A confirmed matching provider receipt establishes test reply success; a Run's final text or success status does not. An uncertain reply remains uncertain and refresh does not resend it. A later uncertain reply cannot erase an earlier confirmed matching reply. Changes to Account credentials/configuration, target configuration or target identity mark previous observations stale. Expiry without receipt is an unobserved test, not evidence of an Agent failure. Closing the wizard, expiry or a later test does not cancel accepted work.

### Bot Conversation History

`GET .../bot/threads` projects retained inbound Account-to-Thread bindings. It returns binding, Session, Thread, current Run and Agent identifiers, Run status, and the Thread update time. It stores no transcript and calls no provider. Administrative disablement or reception changes do not erase history; deleting the Account removes its navigation entry.

The operation requires Account read authority and the intersection of existing Agent-scoped Session, Thread, and Run read permissions. Filtering occurs before pagination. Each row links to the canonical interaction views, which perform their own current authorization. Run status is execution evidence and never constitutes provider reply evidence.

The optional `target_id` filter matches retained trusted inbound Run contexts under the same Account and Thread, subject to current history authority. It does not trust user-editable labels, infer membership from matching Agent IDs, or depend on retained admission batches or a still-existing target. The collection uses bounded keyset pagination ordered by Thread update time and binding ID; cursors are bound to the principal, Workspace, Account, and target filter.

### Bot Reply Observations

Slack and Feishu inbound native replies retain one observation per invocation under the trusted Account, inbound binding, Run and Attempt. Argument validation occurs before observation creation. Before provider I/O, Service commits a `dispatching` marker after checking the current Attempt, retained native context, Account version and credential generation. Failure to create this marker prevents dispatch. No SQL transaction spans provider I/O.

A typed successful provider receipt yields `succeeded`; an explicit native rejection yields `rejected`; ambiguous responses and interrupted dispatch yield `outcome_unknown`. Only the matching provider's typed receipt can establish success. Run completion, model text, arbitrary MCP results and incoming event acceptance cannot establish it. The observation contains correlation identifiers, Account version, credential generation, timestamps, a bounded safe reason code and confirmed provider receipt identifiers. It stores neither message content nor credentials. Receipts remain hidden from the model where the native-tool contract requires that.

Completion evidence can be recorded after the sending Attempt loses authority, because recording an already performed effect grants no execution permission. Failure or timeout while recording completion leaves an unconfirmed marker and does not turn a confirmed external reply into a retryable tool failure. A `dispatching` marker whose Attempt is no longer current, running and leased is presented as `outcome_unknown`, with no invented completion time. Observations are not a dispatch queue: reading or refreshing them never resends a message, and unknown outcomes do not authorize a resend.

`GET .../bot/replies` requires an exact `run_id`, Account read authority and existing Agent-scoped Session, Thread and Run read authority. It returns only that Account's observations for the selected Run, using bounded keyset pagination and principal/Workspace/Account/Run-bound cursors. A disabled Account retains authorized history; a deleted Account is concealed. Observations follow their Account, inbound binding and Attempt retention and do not create a second transcript store. Provider confirmation means the provider accepted a message, not that a person read it. An empty observation list is not proof that no reply was ever sent, particularly for runs predating observation recording.

### Bot Installation Checks

`GET .../bot/setup` requires Account management authority and returns the exact account event path plus an absolute event URL only when the deployment has a configured Connectivity public origin. The URL never derives from browser or forwarded request headers. When the origin is absent, the response retains the path and a null URL; Console explains that deployment configuration is required rather than inventing a reachable endpoint. Reading setup does not contact a provider, expose credentials, enable reception, or imply completed URL verification.

Slack and Feishu Accounts expose `POST .../bot/checks` with an exact `expected_version` and optional `conversation_id`. The operation reads the provider using the Account's current encrypted credentials. It does not send a test message, enable reception, or grant memory access. Slack checks compare the returned App, team, enterprise, and Bot user identity with the Account. Feishu checks compare the token-bound App, tenant, and Bot identity. Manually entered IDs are not verification evidence.

A check returns the observed installation and, when requested, conversation details, a check timestamp, credential generation, and a safe error code on provider failure. Installation activation, conversation membership, audience, and administrative Account availability remain separate facts. A missing membership field is unknown; it is not membership. Discovery uses `GET .../bot/conversations` with bounded provider pagination and returns candidate IDs and names only. A discovered candidate is not an access grant. Provider discovery and checks require Account management authority.

Checks run outside database transactions with a bounded total deadline. Before saving or returning a result, Service rechecks current Account authority, version, and credential generation. Conversation checks also recheck the exact target identity/version, including whether it existed when the check started. A change during the operation invalidates its result. Concurrent checks retain the result of the most recently started completed check; an older check cannot overwrite that result. `GET .../bot/checks/latest`, authorized by Account read access, returns the latest saved check for the selected conversation or installation. Credential rotation hides observations from the previous generation. A failed completed check replaces an earlier success; cancellation does not manufacture a completed observation. These snapshots are timestamped setup evidence, not permanent proof of live membership or successful event reception, Run acceptance, or reply delivery.

## Invariants

1. One Account is one concrete provider identity in one Workspace.
2. Reception is embedded Account configuration; exact targets belong to that Account.
3. Credential rotation and reception changes preserve identity and Thread correlation.
4. Account disablement blocks dispatch; reception closure alone does not revoke accepted replies.
5. Account use, action allowlists, and target authority are separately validated.
6. Recovery never replaces a missing or disabled account with another account.
