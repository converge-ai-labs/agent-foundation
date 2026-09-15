# Bots Integration

## Design Position

Console provides a Bot-oriented experience for customer-owned Slack and Feishu applications. This contract owns onboarding, navigation, response configuration, and the entry to [Bot Memory](bot-memory.md). Application Account identity and credentials, messaging execution, IAM, and backend operations retain their existing owners.

The journeys below define product behavior; they do not introduce HTTP routes or declare new backend capabilities available. Controls that depend on an unavailable Service operation remain unavailable with an explanation. A prototype never substitutes for server-side validation.

## 1. Product Outcome

A Workspace administrator connects a customer-owned Slack or Feishu application, selects an existing Agent, and lets that Agent participate in external conversations. Operators can then manage where it responds, inspect its conversations, and access the associated memory management surface without dealing with credentials during everyday work.

The first release supports Slack and Feishu customer-owned applications. It requires no official a13n marketplace application, hosted installation broker, or external Connector service. Existing Lark support remains intact, but a separately localized Lark onboarding journey is outside this release.

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
| Bot Memory             | Scoped records and metadata, browsing, sharing, and retention; owned by [Bot Memory](bot-memory.md)                                 |

### Bot identity

A Bot is a managed view of one Slack or Feishu Application Account, not a second independently mutable identity. Bot URLs reference the Account ID. There is no second credential store, duplicate Agent assignment, independent Bot enabled flag, or required `bot_id` in this product boundary.

A single Account has one default Agent; an exact target can select another Agent through the existing advanced target configuration. One Agent can serve several Bots. One Bot does not span multiple platforms or external installations in this product boundary.

Bots lists supported messaging Accounts, including existing Accounts with reception disabled. Creating an Account from the Bot wizard makes the same Account visible in Application accounts. Updates from either surface agree after refresh and use the same version preconditions. GitHub Accounts remain in Application accounts and do not become chat Bots.

Slack Workspace and Feishu enterprise are external identity labels, not new a13n Workspaces. A channel/chat name is display metadata, never a routing or authorization key.

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

| Route suffix below `/workspace/{workspaceKey}` | Page                                            |
| ---------------------------------------------- | ----------------------------------------------- |
| `/bots`                                        | Bot collection                                  |
| `/bots/connect`                                | Customer-owned application onboarding           |
| `/bots/{accountId}`                            | Overview                                        |
| `/bots/{accountId}/channels`                   | Channels or group chats                         |
| `/bots/{accountId}/channels/{targetId}`        | Exact target configuration                      |
| `/bots/{accountId}/conversations`              | Authorized projection of existing conversations |
| `/bots/{accountId}/memory`                     | Scoped memory browser                           |
| `/bots/{accountId}/settings`                   | Reception defaults and linked Account settings  |

These browser routes do not declare corresponding new HTTP API resources. Session and Agent detail links reuse their canonical routes. There is no standalone Memory sidebar item in this product boundary.

## 4. Pages and User Journeys

### 4.1 Bot collection

The primary action is **Connect bot / 接入机器人**. A compact toolbar filters by platform and observed setup condition and searches names or external organization labels.

Rows show Bot name, platform, external workspace/enterprise, administrative availability, latest reception/test observation, configured target count, and default Agent. Configured target count is not claimed to be the total number of groups the provider Bot has joined. Display names are local labels; renaming one does not rename the upstream application.

Row activation opens detail. An incomplete setup shows a resume action. Empty state explains customer-owned applications and offers Connect bot. A permission-limited viewer sees permitted metadata without credential or creation actions.

### 4.2 Connect bot wizard

The wizard has five stages: **Platform and account → Connect → Verify → Agent and reception → Test**.

1. Select Slack or Feishu. Reuse an eligible existing Account or connect a new application. An Account already represented in Bots opens its existing page rather than creating a duplicate.
2. Follow the provider-specific instructions below. Required credentials use write-only fields. Provider identity is resolved or verified by trusted API responses and authenticated events where possible; manual identifiers are an advanced fallback, not trusted proof of identity.
3. Save the Account with reception disabled. Display its exact HTTP event endpoint, explain how to configure provider verification, and track verification independently from saving credentials. Challenge validation works before production reception is enabled.
4. Select an existing same-Workspace Agent and an eligible execution Service Account. Explain that the latter controls the permissions under which incoming messages execute. No browser login or external sender supplies Service authority. New Agent creation is a linked flow that preserves the non-secret wizard draft.
5. Configure one pilot channel/chat, explicitly activate reception, and ask the user to send the displayed test message. The UI explains that this invokes an Agent and may consume model usage. Observe event receipt, Run acceptance, and actual provider reply separately. Ending or closing the wizard does not cancel accepted work.

New Bot setup uses explicitly configured targets only as its initial reception scope. The pilot target is therefore the only admitted target until the operator enables others. An explicit **All accessible conversations** setting can retain current Account-default routing behavior. Service must enforce this admission control before the Console can offer it; missing-target fallback alone cannot enforce it. Existing Accounts retain their current behavior until explicitly changed.

Credentials are cleared after submission or dismissal, never written to browser persistence. Non-secret progress can be resumed from saved Account and setup observations. Uncertain saves reconcile using the existing idempotency contract; they do not create a second Account.

### 4.3 Slack onboarding

Provide an application manifest template and a link to Slack's developer console. The deployer creates the application in their own development Workspace, enables the necessary Bot scopes and supported events, and installs it in the intended Workspace. The wizard accepts the Bot token and signing secret, verifies the App/team/Bot identity, and supplies the event request URL.

Explain invitations into pilot channels and the additional permissions needed for selected interaction modes. Increased scopes require the deployer to complete the upstream authorization step. The wizard does not promise to bypass Workspace administrator approval. This is customer-owned application setup, not our own multi-tenant OAuth installation service.

### 4.4 Feishu onboarding

Guide the deployer through creating an enterprise custom application, enabling Bot capability, configuring message permissions and event subscriptions, and publishing an application version with the correct availability range.

The current implementation path uses HTTP events. The wizard supplies the request URL and handles verification-token and optional encryption-key configuration alongside the App credentials. It validates App, tenant, and Bot identity. It explains that searching for the Bot and adding it through group settings can fail when the application has not been published or the operator is outside its availability range.

Do not display a long-connection option until a corresponding Service transport is implemented and validated. No Feishu store application credentials or app-ticket lifecycle are implied by accepting custom-app credentials.

### 4.5 Bot detail

The heading shows local name, platform, external installation identity, administrative availability, and time-stamped setup observations. Tabs are **Overview, Channels / Group chats, Conversations, Memory, Settings**.

- Overview shows default Agent, reception mode, incomplete setup steps, and recent failures with specific recovery actions.
- Channels / Group chats shows exact targets and their Agent and response policies. Provider discovery is advisory and bounded by permissions. Manual ID entry remains available when discovery is unavailable; missing search results are not proof that a private conversation does not exist.
- Conversations filters the existing Session/Thread views by trusted Account/target bindings. It creates no new transcript store and grants no additional history access.
- Memory embeds the Bot Memory contract's three-pane scope tree, record list, and detail browser, with date/type filters and separate sharing settings. It presents records rather than a virtual file tree. Before that feature exists, do not render a working-looking empty memory store or an enabled memory toggle.
- Settings shows inherited Account defaults, reception scope, and links to write-only credential maintenance. Account availability is edited through the canonical Account operation.

Development reference: the [Memory frontend specification and embedded prototypes](bot-memory.md#8-frontend-and-request-behavior) show the three-pane browser and receiving-group view. [Selected-record sharing](bot-memory.md#5-sharing-selected-records) and [group-sharing settings](bot-memory.md#6-continuing-sharing-between-groups) embed their corresponding interaction prototypes. Reuse the same Bot shell; these images introduce no independent Bot identity or backend capability.

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

The Bot Memory contract owns group-local memory, daily/long-term record organization, selected-record publication, and continuing sharing between eligible groups. Sharing settings distinguish the Bot's external platform space from its a13n Workspace. Sharing grants retrieval, not permission to send messages or edit another group's records. Memory exposes per-record Share actions and group-sharing settings; the latter select participants, content kinds, history, and future enrollment.

Memory follows the exact conversation across target Agent changes. Execution must use trusted conversation memory rather than automatically adding the Agent's ordinary agent/user memories. Memory Provider selection and root/child/recovery integration require an extension of the accepted Memory contract. Metadata, source/date fields, and complete date listing are not available merely because the backend is Mem0; verified backend and API changes are required.

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

## Compatibility and Backend Boundaries

Existing Application Accounts remain the identity and credential owner, and creating a Bot never copies their credentials. Existing reception behavior remains unchanged until explicitly configured through a supported operation. New target admission, discovery, verification observations, and conversation-memory integration require corresponding authorized Service operations before their controls become usable. Provider permissions limit setup and discovery; the Console does not manufacture missing capability or evidence.

Memory management is owned by [Bot Memory](bot-memory.md). Backend content and completion semantics remain owned by [Service Long-Term Memory](../a13n-service/42-memory.md). This product contract adds no schema migration, execution principal, message transport, credential lifecycle, or parallel transcript store.

## Related Contracts

- [Application Accounts](../a13n-service/40-connectivity/01a-application-accounts.md)
- [Event Reception and Routing](../a13n-service/40-connectivity/01-ingress-and-routing.md)
- [Messaging Reception](../a13n-service/40-connectivity/02-messaging-ingress.md)
- [Console](console.md)
- [Bot Memory](bot-memory.md)
