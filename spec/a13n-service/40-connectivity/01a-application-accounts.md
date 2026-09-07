# Application Accounts

## Design Position

An Application Account is one concrete external identity operated directly by Service in one Workspace. It can be a provider user account, Bot, or application installation. It owns provider identity, API configuration, credentials, and administrative availability independently of event reception. In the Connectivity account namespace its type is `Account`; the product label is Application Account (应用账号).

## Boundaries

| Concern                                                                      | Owner               |
| ---------------------------------------------------------------------------- | ------------------- |
| External identity, API configuration, credentials, availability              | Application Account |
| Reception, default Agent, execution Service Account and default input policy | Application Account |
| Exact provider object Agent and narrow configuration override                | AccountTarget       |

A reusable provider application definition is not an Account. Each concrete provider-tenant installation or authorization is a separate Account. Provider adapters own exact identity meaning and credential schemas. ConnectorProvider, ConnectorConnection, and MCPConnection retain their independent contracts.

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

Account creation starts no Agent. Reception is embedded Account configuration, with no separate Ingress identity or Agent allowlist. Supported transport settings and credentials use the provider-owned Account schema.

Account defaults select input execution authority and the default Agent. Exact targets select an optional Agent and narrow override. Changing defaults preserves `(account_id, external_ref.kind, external_ref.id)` Thread correlation. Each ordinary Run uses current configuration; an active or selected waiting Run receives only Steer.

`active` is administrative eligibility, not a health claim. A disabled Account blocks reception and subsequent provider dispatch, including from accepted Runs. Re-enabling it does not change `receive_enabled`.

Setting `receive_enabled=false` on an Account or exact target stops new admission; already acknowledged batches continue processing. Accepted replies retain their protected target and action policy and continue checking Account availability, execution Principal, and Attempt fencing. Reception settings do not grant proactive authority.

Account deletion makes the identity unavailable and clears its credentials. Retained Run, Event, Batch, and Binding evidence keeps its original identity; recovery never substitutes another Account. A newly created Account inherits no retained Run authority.

## Default Tools and Authority

Application Account tools are default host-injected capabilities. They do not belong to Agent configuration, Revisions, overrides, or user-selected connection lists. The trusted Run entry authorizes the Account, exact actions, and provider-typed target scope and freezes them as protected Run context. Inbound admission supplies current-conversation authority; an independent trusted entry can authorize proactive operations without reception. No context means no Account tools, and Service never enumerates all Workspace Accounts to derive defaults.

Account use and target authority are distinct. Binding an Account requires current `application_account.use` authority. An allowed operation does not authorize targets outside the entry's scope. The entry must establish target authority from its trusted policy; validating a target's shape is not permission to use it. Proactive operations expose provider-native destination arguments only within that scope. Current-conversation reply operations use the admitted target and expose no destination selector. Inbound reply authority does not enable proactive send tools.

The [Agent-facing tools contract](04-agent-facing-tools.md#default-native-tool-contexts) owns protected contexts, runtime composition, and continuation behavior. An Account may contribute proactive actions and a narrower inbound reply surface to the same Run; each retains its own scope. Credentials resolve at execution time. Tool arguments cannot choose another Account, credential, provider tenant, or API origin.

Workspace Admin manages Accounts and credentials. Viewer has safe metadata read access; Runner, Builder, and Admin use Accounts only through authorized Run contexts. Binding and dispatch check current Workspace authority and resource eligibility; an external sender is never a Service Principal.

## Built-in Proactive Scopes

All target collections contain at most 128 entries; an empty collection authorizes no targets. Target scopes reject unknown fields. Account tool names are exact action allowlists, with no wildcard or all-tools mode.

| Provider | Tools                                                                                           | Target scope                                                                   |
| -------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Slack    | `slack.send_message`                                                                            | `channel_ids`: exact channel IDs                                               |
| Lark     | `lark.send_message`                                                                             | `chat_ids`: exact chat IDs                                                     |
| GitHub   | `github.add_comment`, `github.read_comments`, `github.read_issue_or_pr`, `github.list_pr_files` | `repositories`: unique entries with `repository_id`, `owner`, and `repository` |

Slack and Lark send actions accept the selected destination ID and provider-specific message content. GitHub actions accept `repository_id`, positive issue/PR `number`, and `target_kind` (`issue` or `pull_request`), plus their bounded action content. The configured owner/name locates the API resource; the native client obtains an installation token restricted to the exact immutable repository ID before dispatching the target action. PR file listing requires a pull-request target. No action accepts a caller-selected API origin or credential.

## Management API

Account management uses `/api/v1/workspaces/{workspace_id}/application-accounts` for create/list and `/api/v1/application-accounts/{account_id}` for get/update/delete. Credential replacement uses `PUT .../credentials`; administrative commands use `POST .../enable` and `POST .../disable`. Creation and commands follow shared idempotency rules; mutations require exact version preconditions. Responses contain safe metadata only.

Exact object management uses the Account `/targets` child collection. Workspace Builders can manage targets within their current Agent and capability authority. Account management and credentials require Workspace Admin. Possession of an Account or target ID grants no authority.

## Invariants

1. One Account is one concrete provider identity in one Workspace.
2. Reception is embedded Account configuration; exact targets belong to that Account.
3. Credential rotation and reception changes preserve identity and Thread correlation.
4. Account disablement blocks dispatch; reception closure alone does not revoke accepted replies.
5. Account use, action allowlists, and target authority are separately validated.
6. Recovery never replaces a missing or disabled account with another account.
