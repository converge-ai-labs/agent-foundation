# Application Accounts

## Design Position

An Application Account is one concrete external identity operated directly by Foundation in one Workspace. It can be a provider user account, Bot, or application installation. It owns provider identity, API configuration, credentials, and administrative availability independently of event reception. In the Connectivity account namespace its type is `Account`; the product label is Application Account (应用账号).

## Boundaries

| Concern                                                                      | Owner                                        |
| ---------------------------------------------------------------------------- | -------------------------------------------- |
| External identity, API configuration, credentials, availability              | Application Account                          |
| Event transport, subscriptions, input execution Principal and allowed Agents | Ingress                                      |
| Event matching, Agent selection and input policy                             | Route                                        |
| Internal execution authority                                                 | Foundation User or Service Account           |
| Externally managed SaaS credentials                                          | Connector service behind ConnectorConnection |

A reusable provider application definition is not an Account. Each concrete tenant installation or authorization is a separate Account. Provider adapters own exact identity meaning and credential schemas. ConnectorProvider, ConnectorConnection, and MCPConnection retain their independent contracts.

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
    version: int
    credential_configured: bool
    credential_generation: int
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime
```

Account IDs use the `acct_` object prefix. Provider identity, Organization, and Workspace are immutable. Name and identity-preserving settings are mutable under exact version preconditions. Changing provider, installation, tenant, or account creates a different Account. Among non-deleted Accounts within a Workspace the provider-declared concrete identity is unique; mutable display names and transport configuration never determine that identity.

An Account belongs to its Workspace and can serve multiple Agents. An Agent can select several Accounts. The Account does not own a default Agent or an execution Service Account. Those input decisions belong to Ingress, while independently initiated Runs use their accepted authority Principal.

An Account owns encrypted credential material under the shared [credential protection contract](../27-secret-management.md). API credentials and application/installation-wide verification secrets belong to the Account. A provider may define a subscription-exclusive verification secret owned by its Ingress. Credential rotation preserves account and ingress IDs and retained event/thread correlation. Credentials are write-only inputs to owning resource operations, never public Secret selections or Agent input.

## Ingress Relationship and Lifecycle

An Account has zero or one Ingress. Every Ingress has one immutable same-Workspace `account_id`; database uniqueness and tenant foreign keys enforce this relationship. Receive-only and send-only configurations use the same Account model. Account creation neither creates an Ingress nor starts an Agent.

Ingress holds transport/subscription configuration, its immutable execution Service Account, allowed Agents, default Agent, and Routes. Its provider identity is resolved through the Account, never duplicated as separately editable identity configuration. Changing supported transport configuration preserves the Ingress ID. One event selects at most one Route and one Agent; the existing `(ingress_id, external_ref.kind, external_ref.id)` Thread binding remains authoritative.

`active` is administrative eligibility, not a continuous provider health claim. `disabled` Account blocks new admission through its Ingress and every subsequent provider dispatch, including retries and previously accepted Runs. Already dispatched effects retain their provider outcome semantics. Re-enabling the Account does not change Ingress enablement.

Disabling Ingress stops new event admission and rejects pending input that has not been accepted. It does not revoke the bounded reply authority of an already accepted Run. Those actions continue to check Account availability, execution Principal, Route authorization, accepted target and action policy, and Attempt fencing. Explicit Route disablement revokes its action authority. Ingress disablement never grants broader proactive authority.

Account deletion is blocked while an Ingress references it. Accepted Run references remain meaningful; deletion makes the Account unavailable and clears credential material without substituting another identity during recovery. A new Account may reuse a deleted Account's external identity or name, but receives a new ID and inherits no retained Run authority. An Ingress with retained admissions or Thread bindings cannot be deleted in a way that destroys their identity; disablement is the ordinary way to stop reception.

## Selection and Authority

[Agent Management](../28-agent-management.md) owns the `account_tools` category in Agent configuration, Run overrides, and capability overlays. Each selection fixes an `account_id`, exact allowed tool names, provider-typed target scope, and `defer_loading`. The account adapter validates scope; external event content and model arguments cannot define grants. An empty tool list exposes no tools. Duplicate account selections are invalid. Run acceptance fixes account, tool and target scope; runtime uses current eligible credentials and cannot widen that scope.

Account use and target authority are distinct. Selecting an account requires current `application_account.use` authority. An explicit allowed operation does not authorize targets outside the selected provider scope. Proactive operations expose provider-native destination arguments only within that scope. Current-conversation reply operations instead use trusted inbound target context and do not expose a destination selector. Inbound reply authority does not automatically enable proactive send tools. A child Run receives account capabilities only through explicit authorized delegation.

The [Agent-facing tools contract](04-agent-facing-tools.md) owns runtime composition and protected target context. An account may contribute independently selected actions and a narrower inbound reply surface to the same Run; neither source expands the other's scope. Foundation binds account credentials at execution time. Tool arguments cannot choose another Account, credential, tenant, or API origin.

Workspace Admin manages Accounts and credentials. Builder can select authorized Accounts in Agent authoring and Routes. Viewer has safe metadata read access; Runner uses Accounts only through accepted Runs. Every selection and dispatch checks the current Workspace role and resource eligibility; an external sender is never a Foundation Principal.

## Built-in Proactive Scopes

All target collections contain at most 128 entries; an empty collection authorizes no targets. Target scopes reject unknown fields. Account tool names are exact selections, with no wildcard or all-tools mode.

| Provider | Tools                                                                                           | Target scope                                                                   |
| -------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Slack    | `slack.send_message`                                                                            | `channel_ids`: exact channel IDs                                               |
| Lark     | `lark.send_message`                                                                             | `chat_ids`: exact chat IDs                                                     |
| GitHub   | `github.add_comment`, `github.read_comments`, `github.read_issue_or_pr`, `github.list_pr_files` | `repositories`: unique entries with `repository_id`, `owner`, and `repository` |

Slack and Lark send actions accept the selected destination ID and provider-specific message content. GitHub actions accept `repository_id`, positive issue/PR `number`, and `target_kind` (`issue` or `pull_request`), plus their bounded action content. The configured owner/name locates the API resource; the native client obtains an installation token restricted to the exact immutable repository ID before dispatching the target action. PR file listing requires a pull-request target. No action accepts a caller-selected API origin or credential.

## Management API

Account management uses `/api/v1/workspaces/{workspace_id}/application-accounts` for create/list and `/api/v1/application-accounts/{account_id}` for get/update/delete. Credential replacement uses `PUT .../credentials`; administrative commands use `POST .../enable` and `POST .../disable`. Creation and commands follow shared idempotency rules; mutations require exact version preconditions. Responses contain safe metadata only.

Ingress creation accepts `account_id` and ingress-owned configuration. It accepts no account credentials or provider identity fields. Account and ingress routes use their own IAM actions; possessing either resource ID grants no authority.

## Invariants

1. One Account is one concrete provider identity in one Workspace.
2. Zero or one Ingress references an Account; one Ingress cannot change accounts.
3. Credential rotation and reception changes preserve identity and Thread correlation.
4. Account disablement blocks dispatch; ingress disablement alone does not revoke accepted replies.
5. Account selection, action selection, and target authority are separately validated.
6. Recovery never replaces a missing or disabled account with another account.
