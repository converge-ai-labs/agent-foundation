# Bots Integration

## Design Position

Console provides a Bot-oriented experience for customer-owned Slack, Feishu, and GitHub identities. This contract owns onboarding, navigation, response configuration, and the entry to [Bot Memory](bot-memory.md). Application Account identity and credentials, messaging execution, IAM, and backend operations retain their existing owners.

The journeys below define product behavior; they do not introduce HTTP routes or declare new backend capabilities available. Controls that depend on an unavailable Service operation remain unavailable with an explanation. A prototype never substitutes for server-side validation.

## 1. Product Outcome

A Workspace administrator connects a customer-owned Slack, Feishu, or GitHub identity, selects an existing Agent, and lets that Agent participate in external conversations. Operators can then manage where it responds, inspect its conversations, and access the associated memory management surface without dealing with credentials during everyday work.

Bots supports Slack and Feishu customer-owned applications, GitHub Apps, and ordinary GitHub accounts using notification polling. It requires no official a13n marketplace application, hosted installation broker, or external Connector service. Existing Lark support remains intact, but a separately localized Lark onboarding journey is outside this release.

Bots is placed under Integrations alongside Application accounts and Connections. The product label is **Bots** in English and **机器人** in Simplified Chinese.

## 2. Ownership and Scope

| Concept                | Responsibility                                                                                                                      |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| a13n Workspace         | Existing resource ownership and Service authorization boundary                                                                      |
| Agent                  | Reusable instructions, model, skills, tool selections, and execution configuration                                                  |
| Application Account    | Concrete external application identity, credentials, administrative availability, reception defaults, and execution Service Account |
| Bot                    | Product experience over a supported messaging Application Account                                                                   |
| AccountTarget          | Exact external channel/chat, optional Agent override, and supported per-target settings                                             |
| Connection             | Independently authorized outbound tool source usable by the selected Agent                                                          |
| Session / Thread / Run | Existing durable interaction, history, and execution model                                                                          |
| Bot Memory             | Provider-independent index/documents, on-demand reading, sharing, and retention; owned by [Bot Memory](bot-memory.md)               |

### Bot identity

A Bot is a managed view of one supported Application Account, not a second independently mutable identity. Bot URLs reference the Account ID. There is no second credential store, duplicate Agent assignment, independent Bot enabled flag, or required `bot_id` in this product boundary.

A single Account has one default Agent; an exact target can select another Agent through the existing advanced target configuration. One Agent can serve several Bots. One Bot does not span multiple platforms or external installations in this product boundary.

Bots lists supported messaging Accounts, including existing Accounts with reception disabled. Creating an Account from the Bot wizard makes the same Account visible in Application accounts. Updates from either surface agree after refresh and use the same version preconditions. GitHub Accounts also appear in Bots, using repository targets and Issue/PR conversations.

Slack Workspace and Feishu enterprise are external identity labels, not new a13n Workspaces. A channel/chat name is display metadata, never a routing or authorization key.

### External Installation and Workspace Identification

One Application Account represents one concrete application installation identity on one platform. The same Slack application installed in two Slack Workspaces uses two Accounts with their own installation identity and credentials. One Slack Account can participate in many channels, and one Feishu Account can participate in many groups; an Account is not created per group. Slack and Feishu installations remain separate Accounts even when they share a display name or invoke the same a13n Agent.

The a13n Workspace containing the Account is selected through the existing Console Workspace context. It is distinct from the external Slack Workspace or Feishu enterprise. In the Bot wizard, Service verifies supplied credentials and resolves the external installation identity where supported, then displays the verified external name and ID for confirmation. This is not a picker of unrelated Slack Workspaces and does not implement an official a13n OAuth distribution service. Changing the intended external Workspace means supplying its installation credentials, not changing a label. Explicit advanced ID entry remains subject to verification and cannot become proof of identity by itself.

## 3. Information Architecture

```text
Agents
Sessions
Resources
  Models
  Skills
  Environments
Integrations
  Bots
  Application accounts
  Connections
Observe
  Traces
```

Browser routes follow the existing Workspace key boundary:

| Route suffix below `/workspace/{workspaceKey}`        | Page                                            |
| ----------------------------------------------------- | ----------------------------------------------- |
| `/bots`                                               | Bot collection                                  |
| `/bots/connect`                                       | Customer-owned application onboarding           |
| `/bots/{accountId}`                                   | Overview                                        |
| `/bots/{accountId}/channels`                          | Channels or group chats                         |
| `/bots/{accountId}/channels/{targetId}`               | Exact target configuration                      |
| `/bots/{accountId}/channels/{targetId}/conversations` | Exact target conversation history               |
| `/bots/{accountId}/channels/{targetId}/memory`        | Exact target memory browser                     |
| `/bots/{accountId}/conversations`                     | Authorized projection of existing conversations |
| `/bots/{accountId}/memory`                            | Scoped memory browser                           |
| `/bots/{accountId}/settings`                          | Reception defaults and linked Account settings  |

These browser routes do not declare corresponding new HTTP API resources. Session and Agent detail links reuse their canonical routes. There is no standalone Memory sidebar item in this product boundary.

## 4. Pages and User Journeys

### 4.1 Bot collection

The primary action is **Connect bot / 接入机器人**. A compact toolbar filters by platform and observed setup condition and searches names or external organization labels.

Rows show Bot name, platform, external workspace/enterprise, administrative availability, latest reception/test observation, configured target count, and default Agent. Configured target count is not claimed to be the total number of groups the provider Bot has joined. Display names are local labels; renaming one does not rename the upstream application.

Row activation opens detail. An incomplete setup shows a resume action. Empty state explains customer-owned applications and offers Connect bot. A permission-limited viewer sees permitted metadata without credential or creation actions.

### 4.2 Connect bot wizard

The wizard has five stages: **Platform and account → Connect → Verify → Agent and reception → Test**.

1. Choose between equally prominent Use an existing account and Create a new account options. The existing-account option shows selectable Accounts; the new-account option shows a compact Slack, Feishu, and GitHub connection selection. An Account already represented in Bots opens its existing page rather than creating a duplicate.
2. Follow the provider-specific instructions below. Required credentials use write-only fields. Provider identity is resolved or verified by trusted API responses and authenticated events where possible; manual identifiers are an advanced fallback, not trusted proof of identity.
3. Save the Account with reception disabled. For HTTP display its exact event endpoint; for long connections display platform instructions and poll the current event connection status. Notification polling profiles explain outbound-only reception and display scan health. Track provider identity verification independently from saving credentials and transport connectivity. Challenge validation works before production reception is enabled.
4. Select an existing same-Workspace Agent and an eligible execution Service Account. Explain that the latter controls the permissions under which incoming messages execute. No browser login or external sender supplies Service authority. New Agent creation is a linked flow that preserves the non-secret wizard draft.
5. Configure one pilot channel/chat or GitHub repository, explicitly activate reception, and ask the user to send the displayed test message. The UI explains that this invokes an Agent and may consume model usage. Prepare a uniquely marked test without sending a message, then observe authenticated event receipt, Run/Steer acceptance, and an actual provider-confirmed reply containing that marker separately. Refresh reads observations only; a lost preparation acknowledgement retries the same command, and expired or stale tests require a newly prepared message. Ending or closing the wizard does not cancel accepted work.

New Bot setup uses explicitly configured targets only as its initial reception scope. The pilot target is therefore the only admitted target until the operator enables others. An explicit **All accessible conversations** setting can retain current Account-default routing behavior. Service must enforce this admission control before the Console can offer it; missing-target fallback alone cannot enforce it. Existing Accounts retain their current behavior until explicitly changed.

Credentials are cleared after submission or dismissal, never written to browser persistence. Non-secret progress can be resumed from saved Account and setup observations. Uncertain saves reconcile using the existing idempotency contract; they do not create a second Account.

### 4.3 Slack onboarding

Provide an application manifest template and a link to Slack's developer console. The deployer creates the application in their own development Workspace, enables the necessary Bot scopes and supported events, and installs it in the intended Workspace. The wizard accepts the Bot token plus the signing secret for HTTP, or an app-level token with `connections:write` for Socket Mode. It verifies the App/team/Bot identity and displays either the HTTP request URL or Socket Mode connection status. Socket Mode is app-wide; installations of the same App use the same app-level token.

Explain invitations into pilot channels and the additional permissions needed for selected interaction modes. Increased scopes require the deployer to complete the upstream authorization step. The wizard does not promise to bypass Workspace administrator approval. This is customer-owned application setup, not our own multi-tenant OAuth installation service.

### 4.4 Feishu onboarding

Guide the deployer through creating an enterprise custom application, enabling Bot capability, configuring message permissions and event subscriptions, and publishing an application version with the correct availability range.

The wizard offers HTTP events and WebSocket long connections. HTTP shows the request URL and asks for App ID, App Secret, Verification Token, and an optional Encrypt Key. Long connections ask only for App ID and App Secret, explain the corresponding Feishu event subscription setting, and show live transport status. It does not ask the operator for Tenant Key or Bot Open Id. Before creating the Account, Service queries the official Feishu APIs with the supplied App credentials and derives both identifiers. Failed discovery creates no Account. The saved Account still contains the complete immutable identity, and the Verify stage displays the verified application and enterprise for confirmation. It explains that searching for the Bot and adding it through group settings can fail when the application has not been published or the operator is outside its availability range.

Existing Accounts retain HTTP until explicitly changed. Changing transport preserves Bot and memory identity; the operator updates transport credentials before switching. A connected transport does not replace the end-to-end setup test. No Feishu store application credentials or app-ticket lifecycle are implied by accepting custom-app credentials.

### GitHub onboarding

GitHub offers two explicit connection choices: **GitHub account · Polling** and **GitHub App · Webhook**. Polling explains that a dedicated ordinary account and classic PAT are required, with no public callback. The GitHub.com wizard derives the user identity from credentials before saving; it exposes polling interval and initial lookback without asking for a manually claimed user ID. It explains that repository access and GitHub notification subscriptions are both required. App setup guides installation, Issues/Pull Requests read/write permissions, supported event subscriptions, private key, webhook secret, and a publicly reachable event URL.

Both paths verify identity, select an accessible pilot repository, choose an Agent and execution Service Account, and explicitly enable reception. The sender control accepts exact usernames or `*`; polling explains that unknown senders are ignored with a named list and that notification updates can coalesce. The App wizard offers all supported events or new comments; advanced Account/target policy supports exact event selections. GitHub replies are ordinary Issue/PR comments. The test asks the user to post its marker in a pilot repository Issue/PR, using an allowed sender; polling additionally requires notification delivery, for example through a real mention of the connected account.

GitHub Bot details use **Repositories** in place of Channels/Groups and retain authorized conversation and reply observations. Polling connection details show the latest scan time/error without presenting a webhook field. GitHub has no Bot Memory tab, settings, or repository sharing controls in this profile.

### 4.5 Bot detail

The heading shows local name, platform, external installation identity, administrative availability, and time-stamped setup observations. Tabs are **Overview, Channels / Group chats, Conversations, Memory, Settings**.

- Overview groups information under **Message responses**, **Group memory**, and **Platform connection status**. It shows the default Agent by name with a detail link; loading and unavailable names have explicit states rather than exposing an opaque ID as the label. Message responses summarizes admission and reply behavior; Group memory summarizes Bot defaults and points to per-group visibility; Platform connection status identifies the last verified external installation and app activation, without claiming message delivery or Agent execution succeeded. Incomplete setup steps and recent failures retain specific recovery actions.
- Channels / Group chats shows exact targets and their Agent and response policies. Provider discovery is advisory and bounded by permissions. Manual ID entry remains available when discovery is unavailable; missing search results are not proof that a private conversation does not exist.
- Conversations filters the existing Session/Thread views by trusted Account/target bindings. It creates no new transcript store and grants no additional history access.
- Memory embeds the Bot Memory contract's three-pane conversation/storage selection, `_index.md` topic navigation, and version-aware document browser, with date/type filters and group visibility settings. Authorized knowledge/procedure revisions follow the shared document model; event corrections originate in conversations. TOC and index links open bounded authorized content on demand. Availability distinguishes sandbox-bound storage, configured persistent storage, missing/lost targets, and empty collections. The derived index has no ordinary document mutation actions.
- Settings shows inherited Account defaults, reception scope, and links to write-only credential maintenance. Account availability is edited through the canonical Account operation.

The [Memory frontend specification](bot-memory.md#8-frontend-and-request-behavior) defines the three-pane browser and receiving-group view. [Group visibility](bot-memory.md#5-group-visibility) and [visibility configuration](bot-memory.md#6-visibility-configuration) define their corresponding interactions. All reuse the same Bot shell.

### 4.6 Channel or group-chat detail

Breadcrumbs retain Bot and external organization context. Tabs are **Configuration, Conversations, Memory**.

Configuration includes:

| Field              | Semantics                                                                  |
| ------------------ | -------------------------------------------------------------------------- |
| Receive messages   | Exact target admission toggle                                              |
| Agent              | Inherit Account default or select an authorized same-Workspace Agent       |
| When to respond    | Mention, continue an activated discussion, or all supported human messages |
| Reply placement    | Automatic, provider thread/topic, or main conversation, when supported     |
| Advanced overrides | Existing supported model, skill, and Connection selections                 |
| Memory policy      | Link or embedded controls from the Bot Memory contract                     |

The default experience is one Agent per Bot, with target Agent overrides under an advanced disclosure. Capability restrictions and unavailable selections remain visible with explanations.

AccountTarget does not carry channel-specific instructions. Users edit the selected Agent or choose another Agent; target instructions are not inferred from a prototype.

## 5. Messaging Behavior

Reuse the accepted [Messaging Reception](../a13n-service/40-connectivity/02-messaging-ingress.md) semantics. In particular:

- `mention`: each admitted group message requires a mention.
- `discussion`: a mention activates a discussion; its established continuation can proceed without another mention.
- `chat`: supported messages correlate to the whole conversation, including supported subthreads.
- Direct messages are addressed by construction; they are not group conversations for memory sharing.
- `thread` and `main` force supported placement; `auto` permits a bounded provider-native choice. Top-level group tasks can start a thread under auto. Auto must not be labeled as a promise that every top-level message gets a main-flow reply.
- Placement changes do not change the existing Thread binding. A forced main-flow reply can expose content beyond a topic; topic membership restrictions must not be bypassed.
- An inbound message selects at most one Agent. Idle history continues through ordinary Run acceptance; active work receives Steer with its existing configuration.
- Receiving a message does not send an automatic echo or imply a completed reply. The Agent invokes authorized native reply tools and may remain silent.

Selected modes determine the permissions requested during setup. Unsupported combinations are rejected with an actionable message rather than silently mapped to another behavior.

## 6. States, Permissions, and Failures

Administrative state, reception enabled state, and observations stay independent. A saved credential or active Account is not a health claim. UI labels distinguish **Setup incomplete, Awaiting test, Last test succeeded, Attention required**, with the observation time. Observations become stale after relevant credential or policy changes; a past successful test never overrides current disablement.

| Condition                                           | User-visible behavior                                                      |
| --------------------------------------------------- | -------------------------------------------------------------------------- |
| Invalid credentials                                 | Show the failed verification step; preserve non-secret draft               |
| Missing scope, unavailable app, or Bot not in group | Identify the required provider action and retry only on explicit request   |
| No test event yet                                   | Wait or retry instructions; do not claim failure of Agent execution        |
| Event accepted but Run fails                        | Link the existing authorized Run diagnostic                                |
| Run succeeds without a reply action                 | State that execution completed without sending a reply                     |
| Reply dispatch outcome unknown                      | Show uncertainty; no blind resend or false success                         |
| Concurrent configuration edit                       | Preserve draft and offer reload; no silent overwrite                       |
| Reception disabled                                  | Stop new admission; already accepted work follows existing lifecycle rules |
| Account disabled                                    | Block subsequent native dispatch under the canonical eligibility check     |

Account and credential changes retain Workspace Admin authority. Target configuration retains current Builder/Agent/capability checks. Read-only navigation does not expand access to private conversation transcripts or memory. New discovery/check APIs need explicit authorization and bounded, secret-free observations. The UI never invents inaccessible channel names.

## 7. Memory Integration Boundary

Bots supplies trusted Account identity, external tenant, exact conversation, conversation kind, Thread/Run source, execution principal, and current reception eligibility. Memory determines the permitted read/write scopes and never accepts model-selected raw scope IDs.

The Bot Memory contract owns group-local document memory, bounded `_index.md` navigation, semantic/procedural revisions, append-only events, and on-demand section reading. Each group chooses **Only this group** or **All connected groups**. Opening a group grants eligible groups in the same Bot installation read access to its existing and future current documents; it creates no copies and does not open other groups' memory. Shared readers cannot revise or delete another group's content, inspect its history/diffs, or use private source links. Cross-Account and direct-conversation sharing are excluded. Committed revisions remain immutable; derived navigation follows admitted content and current visibility.

Memory follows the exact conversation across target Agent changes, while availability follows its explicit storage binding. Execution uses trusted conversation memory without unioning ordinary Agent/User memories. Account settings select current-Environment filesystem memory by default or an explicit Provider; credentials stay on their owning Environment/Provider resources. [Account-owned selection](bot-memory.md#account-owned-memory-selection) and [Service Memory](../a13n-service/42-memory.md) own root/child/recovery bindings. Backend capability declarations and validation determine available history, metadata, and traversal rather than the presence of a file-looking UI.

The Memory management tab and its data are restricted to effective a13n Workspace administrators. Setup explains that administrators can manage retained private-group memory without personal platform membership checks. This does not grant cross-group runtime recall. Service stores the lightweight document directory; document bodies remain in the selected Provider.

Bots remains usable with memory disabled or not installed. Adding Memory does not change event deduplication, Thread correlation, native reply authority, or Connector authorization.

## 8. Acceptance Scenarios

01. An existing Slack Account appears in Bots without copying credentials or changing reception.
02. New setup resumes after saving credentials without persisting them in the browser.
03. A new pilot accepts only its configured target; another group cannot activate the default Agent accidentally.
04. Slack and Feishu setup explain their different required provider steps and reject identity mismatch.
05. Mention, discussion continuation, whole-chat activation, and supported placement modes produce the existing canonical Thread behavior.
06. Switching target Agent affects subsequent ordinary Runs, not an active Run being steered.
07. A credential rotation preserves Account identity, conversation links, and memory association.
08. Invalid permissions, missing provider membership, unknown dispatch, and stale test observations remain distinguishable.
09. Every sensitive control is enforced server-side; direct URLs and IDs grant no additional authority.
10. Collection, wizard, and detail pages support English/Chinese, keyboard navigation, narrow viewports, loading, empty, error, forbidden, and stale states.
11. Two installations of one Slack app in different Slack Workspaces remain separate Accounts; entering several channels does not create duplicate Accounts.
12. Credential verification displays the external installation for confirmation without confusing it with the containing a13n Workspace.
13. Changing the selected Agent preserves the Account/group memory binding. Sharing a Memory Provider between Accounts never grants cross-Account memory access.
14. Every Bot memory entry point preserves saved-document immutability while permitting authorized creation, deletion, and sharing. Derived indexes update independently, reveal only authorized entries, and load document content on demand.

## Compatibility and Backend Boundaries

Existing Application Accounts remain the identity and credential owner, and creating a Bot never copies their credentials. Existing reception behavior remains unchanged until explicitly configured through a supported operation. New target admission, discovery, verification observations, and conversation-memory integration require corresponding authorized Service operations before their controls become usable. Provider permissions limit setup and discovery; the Console does not manufacture missing capability or evidence.

Memory management is owned by [Bot Memory](bot-memory.md). Backend content and completion semantics remain owned by [Service Long-Term Memory](../a13n-service/42-memory.md). This product contract adds no schema migration, execution principal, message transport, credential lifecycle, or parallel transcript store.

## Related Contracts

- [Application Accounts](../a13n-service/40-connectivity/01a-application-accounts.md)
- [Event Reception and Routing](../a13n-service/40-connectivity/01-ingress-and-routing.md)
- [Messaging Reception](../a13n-service/40-connectivity/02-messaging-ingress.md)
- [Console](console.md)
- [Bot Memory](bot-memory.md)

## Memory Settings API Boundary

Console reads and updates the [Bot-owned settings resource](../a13n-service/42-memory.md#bot-memory-configuration), using its `expected_version` independently of Account version. Bot summaries supply separate Account and Memory settings projections. Existing-account selection uses the Bot collection endpoint. Generic Application Account forms neither read nor write memory configuration. This changes API ownership without changing the administrator-only management boundary or document browsing behavior.
