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

1. Choose Slack, Feishu, or GitHub first, then choose between equally prominent Use an existing account and Create a new account options. Existing Accounts are filtered by the selected platform on the server; new Accounts retain that platform. If the platform has no Accounts, open account creation directly. GitHub creation offers ordinary-account notification polling and GitHub App webhooks within the GitHub platform, explaining their credential and public callback requirements. Keep the selected platform visible with a Change platform action. Changing platform or GitHub connection type clears unsaved credentials. An Account already represented in Bots opens its existing page rather than creating a duplicate.
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

### Task progress and control

Bots project canonical Run state into platform-native messages. Feishu/Lark uses interactive cards and Slack uses Block Kit messages; GitHub does not yet expose this interaction. There is no separate task execution resource or provider-owned Run lifecycle.

Each newly accepted Feishu/Lark or Slack Run with authorized reply capability receives at most one progress card. It shows queued, running, waiting, completed, failed, or stopped state and links to the canonical Run page under the configured Console origin and Workspace key. Completion describes execution, not task fulfillment or successful delivery of the Agent's answer. An earlier explicit reply may be an interim update and never upgrades that status into a task-success claim. Native reply tools explain that a final outcome or blocker must be sent through the same tool after an interim update; plain model completion is not delivered. The card contains no automatic model output, private tool arguments, or invented completion percentage. Answer content is included only through an explicitly authorized `lark.reply` or `slack.reply` invocation. Steer does not create another card. Whole-chat group input without an explicit Bot mention stays silent until an explicit native reply; receive-only input never sends progress.

The message follows the accepted reply placement. Feishu/Lark direct messages and forced main replies use the main conversation; group thread and auto replies use the source message's thread. Updates replace the existing card. Feishu text is Chinese; Lark and Slack text is English. Slack replies use the accepted root thread timestamp, including forced thread placement in direct messages; auto placement uses the main conversation for direct messages. The Agent's explicit native replies append to this same card; status updates preserve all accepted answer content. The card's placement is fixed at acceptance; conflicting explicit auto-placement is rejected. Quiet whole-chat input creates its card only upon an explicit reply. Older Runs without a card record and additional bound conversations retain their native reply transport.

Control progress updates and Worker replies share one durable delivery lease. Explicit reply content is bounded delivery state (not copied into reply observations), persisted after fresh Attempt/source validation and before provider I/O. Success requires provider confirmation; queuing alone is not success. A lost response returns outcome-unknown. Updates retry the same known message, never a second text message. Slack initial writes with an uncertain result are not repeated, including after a process crash; their persisted dispatch marker prevents duplicate messages. Definite rejection removes only the rejected appended answer. Multiple replies append in lease order. Pending stop requests take priority over new reply claims; releasing an in-flight delivery makes interruption immediately eligible instead of postponing it with the polling delay. Oversized cards are rejected before sending without truncation. Feishu/Lark initial create uses stable provider deduplication and is followed by an answer patch because a deduplicated create may return an earlier status-only card. Sealed status rendering retains answers and removes the stop action.

Slack messages contain at most 32,000 answer characters, divided into sections of at most 3,000 characters within the message block limit. Explicit accessible fallback text is included only when it fits within 4,000 UTF-8 bytes, conservatively respecting Slack's update limit. Longer messages omit top-level text and let Slack derive accessible content from the complete supported blocks; answers are not truncated. Oversized appends fail explicitly before any provider write. Slack interaction requests are acknowledged without a Feishu toast payload; the message updates after a valid stop request.

Only an authenticated external sender from the Run's accepted input batch may stop that Run. The callback binds the Account generation, source conversation, exact card message, Run, and unguessable action token. External senders do not become Service Principals. Current execution Service Account authority is checked when accepting the click and again before canonical Run interruption; revoked authority fails closed. Forwarded, unrelated, stale, and forged cards cannot control another task. The acknowledgement means a stop was requested, not that execution has already stopped. Only queued and running Runs offer stop. Waiting is a sealed Run outcome: its card instead asks the user to open details to provide feedback or continue in a successor Run. Sealed cards remove the stop action and stop polling; repeated requests are idempotent, and completed external effects are not reversed.

Card delivery and stop requests survive process restarts. Control-role workers hold bounded delivery leases and never hold a database session across provider calls or Run interruption. Provider callbacks only authenticate and persist a control intent; they never submit model input or execute external calls. Progress-only delivery retries are bounded and do not fail the Run. Explicit reply failure remains visible through the native tool outcome and reply observation, without silently sending a second message. Feishu/Lark initial delivery uses a stable provider deduplication key and expires before its one-hour deduplication window, measured from the first delivery attempt rather than Run acceptance. Account disablement, replacement, or lost execution authority blocks subsequent delivery and control. Run deletion removes its delivery coordinates.

The additive progress table requires no backfill or rewrite of existing Runs. Old processes ignore it, and only new accepted Runs acquire progress delivery records. Roll out the migration before new control/connectivity processes. On rollback, stop progress workers and callback handling before dropping delivery records; canonical Runs and provider messages remain intact.

### Scheduled channel tasks

Slack channel Runs expose Bot-owned tools to propose and list scheduled tasks. A task belongs to one Account, exact channel, authenticated requester, and accepted execution source. Model arguments cannot select a destination, Account, execution principal, or creator. Multiple-requester input batches and scheduled Runs do not receive these management tools. Child Runs do not inherit them. Feishu and GitHub do not expose this interaction yet.

A task supports one absolute future timestamp or a local daily/weekly time with selected weekdays. An explicit IANA timezone is required and displayed in its confirmation card; ambiguous times require clarification. The scheduler skips nonexistent daylight-saving times and uses only the first occurrence of a repeated local time. A pending create or edit does not activate or replace a schedule until its requester confirms the exact Slack card within 24 hours. Existing settings remain active while an edit awaits confirmation. Channel listing includes pending changes, next execution, latest canonical Run status, and bounded admission/delivery errors. The requester can ask for edits or pause, resume, and delete using the card. Deletion stops future acceptance; it does not cancel already accepted Runs or remove their history.

Callbacks authenticate the installation and bind the Account, channel, exact message, requester, and rotating unguessable action token. Stale, forwarded, cross-channel, and other-user actions have no effect. Confirmation and resumption revalidate current execution authority. Stopping future work remains possible after execution authority changes. Tasks retain their accepted Account/target configuration versions; source changes pause execution rather than silently replacing identity or Agent authority. Each Account supports at most 100 non-deleted tasks across its channels. Listing is bounded and paginated.

The Control role reconciles persisted schedules using bounded leases. Each due occurrence starts a fresh canonical Run, with the original channel's narrow native context and current Bot memory selection. Run admission and advancing the schedule commit atomically under the task version and claim fence. The occurrence uses a stable idempotency key, so crashes, replica overlap, and callback races do not duplicate acceptance. Active or waiting occurrences block overlap. Missed intervals coalesce into at most one late execution, followed by the next future occurrence; resuming does not replay a backlog. Admission retries are bounded and eventually pause with a visible error. Account/target reception and execution IAM are checked again before committing a Run. Scheduled Runs have no scheduling-management tools.

Each occurrence uses the normal task-progress card in the channel main flow, including requester-only stop and explicit native result delivery. Execution completion and result delivery remain independent. Confirmation cards use a separate fenced delivery lease; known-message updates retry, while an uncertain initial Slack write is never repeated automatically. Listing reports that unknown outcome without claiming activation. Deleting the originating Account or source Run removes its dependent schedules. Application shutdown/restart retains schedules. The application must be running for due work to be accepted.

The additive task table requires no existing-row scan, rewrite, or backfill. Its indexes are built on an empty table; foreign keys briefly lock referenced Account/Run tables under the migration timeout. Deploy the migration before new Workers and Control processes. Older processes ignore the table. Rollback disables the new scheduler and management tools while retaining records; downgrade deletes schedules and requires deliberate data-loss authorization. Canonical Runs and Slack messages remain independent.

### Event-triggered channel tasks

The same channel-task tools, requester confirmation, management cards, execution authority, and Control reconciler support event triggers as an alternative to a calendar schedule. A task selects exactly one trigger. The model-facing `event` trigger contains `source_target_id`, a namespaced `event_type`, adapter-validated scalar `filters`, and `once` (default true). `once=true` completes after one accepted occurrence; `once=false` keeps watching distinct occurrences until paused or deleted. GitHub App webhooks provide `github.pull_request.merged` (an exact PR's `pull_request.closed` with `merged=true`) and `github.workflow.failed` (`workflow_run.completed` with `conclusion=failure`, optionally filtered by exact branch and workflow ID). Success, cancellation, timeouts, and ordinary PR closure do not satisfy failure/merge conditions. No model is used to evaluate conditions or wait for events. Each matching occurrence starts a canonical Run for the confirmed instructions and native channel delivery, with `trigger_type="bot_event"`; model execution and external delivery retain their ordinary costs and failure semantics.

The Agent discovers sources using `bot_routines.event_sources`. Each result identifies its provider and exact target, supported event types with their filter JSON schemas, and provider setup requirements. Unknown event types, extra filter fields, and invalid filter values fail before a proposal is stored. Event sources are registered native-provider capabilities; unregistered providers and unsupported configuration profiles are not advertised. Sources are active, explicitly configured targets in the destination Workspace and organization, using the same Agent and execution Service Account as the requesting channel Run. The current principal must retain Account-use and Agent-invoke authority. This restriction uses administrator-configured target/Agent assignments and never grants access to every repository merely because its Account exists. A target's verified display name is discovery evidence, not authorization. Unknown names require target verification rather than guessed identity. A confirmation shows the source Account, target kind and ID, event type, filters, once/ongoing behavior, and original channel destination, including that source information will be shared to that channel. Model arguments cannot change that destination or execution principal. Confirmation checks the exact source configuration shown in the proposal. Account/target versions and execution authority are rechecked before future Run acceptance; changed authority blocks execution and pauses the task. Resumption cannot silently adopt changed source authority; recreate the task or confirm an explicit edit.

Connectivity authenticates the webhook before application-owned event matching. Within the ingress transaction, matching writes a durable occurrence; no external call or Agent execution happens there. Repository ID, source Account, activation time, and sender policy bound matching. Explicit subscription conditions are independent of the source Bot's conversational event-action filter. Workflow completion events are subscription-only and never create an Issue/PR conversation. An occurrence stores bounded event facts and a link built from the configured GitHub origin, not repository bodies or arbitrary payload URLs. Match identity uses the PR merge or workflow Run ID plus attempt number, independently of webhook delivery GUID. Each confirmed activation has its own generation. Duplicate deliveries and new delivery IDs for the same occurrence do not duplicate acceptance. Occurrence evidence is retained for the task's lifetime; deleting its originating Account/Run cascades to its source and occurrence records.

Each task buffers at most 100 pending occurrences. Capacity exhaustion returns a retryable ingress error rather than acknowledging lost work. Distinct CI attempts queue while an earlier Run is active. A task lease and version fence protect canonical Run acceptance and occurrence advancement in one transaction. Editing, pausing, or deleting fences an in-flight claim. Confirmed edits and resumption discard pending backlog and start a new observation window; events predating the activation second are not replayed. GitHub timestamps have second precision, so events within the activation second remain eligible. Event completion means Run acceptance, not confirmed external delivery. Event-triggered Runs receive no task-management tools.

Service owns source authorization, confirmation, durable occurrence storage, deduplication, backpressure, and fenced Run acceptance without provider payload assumptions. Trusted native-provider adapters own the supported configuration profiles and target kind, discoverable event catalog, strict filter validation, sender policy, semantic occurrence identity, timestamp precision, and bounded safe facts. Adding a source adapter does not add another task lifecycle or model-facing management tool. Adapters operate only on authenticated normalized ingress; they perform no external I/O or Agent execution during matching. An adapter capability does not itself create a platform connection or grant access to more targets. GitHub is the current production event-source adapter; no generic unauthenticated Webhook, polling monitor, or additional platform source is exposed. Slack owns the current confirmation and destination interaction. Feishu can reuse the common event lifecycle but requires its own task cards and delivery integration before exposure. GitHub notification polling does not supply these event conditions; this workflow requires an App webhook endpoint, Pull request and Workflow run subscriptions, and the relevant repository permissions including Actions read for workflow events. Generic checks, arbitrary event rules, polling fallback, and Slack direct-message destinations are not part of this workflow.

The source and occurrence tables are additive, with no existing-row backfill or rewrite. Indexes are built on empty tables; foreign keys briefly lock the existing task/Run tables under bounded migration timeouts. Migrate before starting the updated processes. Calendar task definitions retain their existing shape when serialized. Event definitions require the updated task reader: upgrade all task Workers and Control processes before enabling event tasks; mixed old/new task processing and binary rollback with retained event tasks are unsupported. Pause event tasks before disabling their processing; retain the updated reader when inspecting their records. Application rollback should disable event creation and processing while retaining the additive records. Schema downgrade removes event sources and occurrence evidence and requires explicit data-loss authorization.

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

## Images and files

Slack and Feishu setup explains the attachment permissions, input limits and model/Environment prerequisites defined by [built-in adapters](../a13n-service/40-connectivity/07-built-in-ingress-adapters.md#message-attachments-and-file-results). The Slack manifest includes `files:read` and `files:write`, with an explicit reauthorization reminder for existing installations. Feishu setup calls out message-resource/upload permissions and publishing the updated application.

Users attach images, PDFs or text files to messages that activate the Bot. Generated files are returned as native attachments in the original conversation/thread, alongside the task progress message. File generation requires an Agent Environment and the explicit Publish asset capability; enabling Bot reception does not implicitly grant code execution or publication. Unknown file-send outcomes must not be presented as confirmed delivery.
