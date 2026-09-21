# Use Console

Console is the browser application for Service. It manages Workspace resources and conversations through the TypeScript Service SDK. It is not the optional Harness UI browser server and does not execute Agents in the browser.

## Open and sign in

Open the deployment's Console origin and use `/login`. The underlying `/api/v1/auth/login` is a JSON POST endpoint, not a page. Initial administrator invitation, password recovery, and email confirmation use the links issued by Service. The browser routes include `/forgot-password`, `/reset-password`, `/confirm-email`, and invitation acceptance.

For repository development, `make dev` starts Service and Console with the local development configuration. Console normally listens at `http://127.0.0.1:5173`, proxying `/api` HTTP and WebSocket traffic to Service on port 8000. `A13N_CONSOLE_SERVICE_URL` changes that upstream. Follow the [local development guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/dev/service/README.md) for dependency startup, fictional seed data, reset behavior, and shutdown; do not use seeded development identities in production.

Use the account menu to select English or Simplified Chinese. Workspace navigation uses Organization and Workspace keys, for example `/:organizationKey/:workspaceKey/agents`. Renaming a display label does not change its key; changing a key invalidates old readable addresses without redirect aliases.

## Configure before running

1. Choose the intended Organization and Workspace.
2. Add an accessible Model Provider, supply its credentials, and create an enabled Model. The public model catalog and connection tests can make remote requests; Model creation does not discover models from the Provider.
3. Create an Agent with instructions, Model selection, and the required input/protocol settings. Save the definition before starting a conversation.
4. Add optional tools, Skills, and an Environment only when the Agent needs them.
5. Start a Session, select the Agent, and submit input. Watch Run status as well as output.

Console permission hints determine which controls appear; the Service authorizes every operation. A hidden or disabled control is not an invitation to bypass the same permission through another client.

## Find the right page

The following routes are relative to `/workspace/:workspaceKey`:

| Page                 | Route                                                     | Work supported                                                              |
| -------------------- | --------------------------------------------------------- | --------------------------------------------------------------------------- |
| Agents               | `agents`, `agents/new`, `agents/:agentKey`                | Definitions, configuration, immutable revisions, lifecycle controls         |
| Conversations        | `sessions`, `sessions/new`                                | Sessions, Threads, Runs, live/retained output, attachments                  |
| A specific Run       | `sessions/:sessionId/threads/:threadId/runs/:runId`       | Chat and Debug views: output, feedback, control actions, execution timeline |
| Models               | `models`                                                  | Models and configured Providers                                             |
| Search               | `settings?section=providers&category=search`              | Provider accounts, tests, and references                                    |
| Skills               | `skills`, `skills/:skillId`                               | Uploads, resources, immutable revisions, and default selection              |
| Environments         | `environments`, `environments/instances`                  | Templates, Provider configuration, actual runtime targets                   |
| Application Accounts | `application-accounts`, `application-accounts/:accountId` | Provider reception and object-specific routing                              |
| Connections          | `connections`                                             | Connected accounts and remote MCP servers; unified search and authorization |
| Traces               | `traces`, `traces/:traceId`                               | Authorized backend diagnostics or an explicit unavailable state             |
| Workspace settings   | `settings`                                                | Membership, invitations, keys, service accounts, permissions, and settings  |
| Media understanding  | `settings/media-understanding`                            | Image, video, and audio models for agents whose model cannot read them      |

Provider management lives in Workspace or Organization settings under `section=providers`, with `category=connectors` for Composio. The Connections dialog combines Provider applications, the deployment-resolved Remote MCP catalog from Service, and a custom URL; saved accounts and servers retain their separate backend resources. Operators can extend or override the catalog through Service configuration without rebuilding Console.

Workspace settings → **Media understanding** holds the image, video, and audio defaults for agents whose own model cannot read that media. Each row saves on its own as soon as you change it, so there is no Save action; a kind with no eligible model says so and links back to Models. The Models page no longer configures them, but it marks the models a default currently selects. Agent and run options override a kind for one agent or one run.

Organization settings live at `/organization/settings`; personal settings live at `/settings/profile`. Profile images, active sessions, password/email changes, and security activity belong to their identity settings, not Agent configuration.

## Reuse an Environment across Sessions

In conversation **Options**, choose **Create from template** to allocate a new Environment, or **Reuse existing** to use an Environment that already exists. Existing choices include the name and ID. To continue work on files from another Session, select that same Environment; choosing its template creates a different one.

Open Run details to see the selected Environment's name and copyable ID. Its **Details** action opens the current status, generation, activity, and effective retention policy. Managed retention is frozen when the Environment is allocated, so editing the template affects new Environments only. **Disabled** means that automatic action is off. Externally owned targets have no Service-managed stop/delete policy.

Idle time starts when no Runs actively use the Environment. Both automatic stop and delete deadlines count from that time; stopping does not restart the deletion timer. A stopped target resumes when used again, retaining its files according to the Provider's storage behavior. A deleted managed target is automatically created fresh on its next use, using the frozen template revision. Its Environment ID and history remain, but old files are not restored.

Manual **Stop target** and **Delete target** appear only when the Provider supports the action. They require management permission and no active users. Sprites automatically sleep when idle and do not offer manual stop. Command status can remain pending after the request is accepted. Keep the details dialog open to see completion or failure and the refreshed Environment state. Deletion is destructive even though a later Run can create a fresh target.

## Set up and manage memory

1. Open an Agent's **Memory** section. Enable **Personal preferences** to remember each user's language, working style, and background, or **Project memory** to keep requirements, decisions, and procedures in editable files. You can use either independently or both together.
2. Personal preferences needs an enabled Mem0 Provider with credentials. **Manage memory providers** opens setup in a new tab without losing the Agent draft. Choose Mem0 OSS or Mem0 Platform under Workspace settings → Providers → Memory; organization administrators can also provide a shared backend. Saving a Provider does not test connectivity.
3. Project memory works without a Provider resource. Files live in the run Environment and follow its storage lifetime. To reuse documents across conversations, select **This agent** and reuse the same Environment. Selecting a scope alone does not preserve or copy files.
4. Personal preferences enables automatic recall by default. The Agent uses each entry's purpose to choose its memory tools; these presets do not add a hard-coded content classifier or automatically save every message. Open **Personal preference settings** or **Project memory settings** inside the enabled option to adjust its recall limits, file storage, memory tools, or optional organization. **Add custom memory** creates a separate entry when you need another purpose or storage configuration.
5. Save the Agent revision to apply changes. Turning an option off leaves saved memory intact. Use **View agent memories**, the Session memory shortcut, or **Memories** in the Workspace sidebar to inspect authorized content. Contextual links open separately to preserve unsaved work.

Existing custom configurations appear separately, each with its own expandable editor. Missing, disabled, or inaccessible Providers do not clear an Agent's saved selection. Provider catalog errors do not mean no backend exists. File memory remains configurable without a Mem0 connection.

The list shows loaded records, 20 per local page. A bounded backend response is not a total count or proof that an empty collection is exhaustive. **Load more records** appears only when the backend provides a continuation cursor. Semantic search runs independently against the backend and displays ranked matches; it is not a filter of the loaded page.

Open a record to inspect or edit it. Add and edit preserve text exactly, up to 8,000 characters. Delete requires confirmation. Read-only users can inspect but do not receive write controls. Provider administration does not grant content access; the Service checks the actual subject, including direct Agent grants and a Thread's current Run.

If a write cannot be confirmed, do not submit it again blindly. The dialog keeps the draft and offers **Inspect current state**. For adds, it shows semantic matches; a missing match does not prove that the add failed. Explicit acknowledgement permits another attempt, which may still create a duplicate. Provider creation has a similar saved-collection reconciliation flow, without exposing or comparing credentials.

Changing a Provider selection does not migrate old records. Select the old Provider on the Memories page to inspect them while it remains enabled and eligible. Records are saved through explicit memory tools. File documents support version history and optional automatic organization; enabling it can make additional model calls. See [long-term memory](memory.md) for backend setup and API semantics.

## Work with conversations

A Session can contain Threads, and each Thread can contain multiple Runs. Use the selected Run's state and version to understand which action is currently valid:

- Waiting feedback answers the specific pending action or client-tool contract.
- Steering adds guidance to active work; acceptance is not proof it has been consumed.
- Interruption requests a stop; it does not roll back completed side effects.
- Branching creates separate lineage rather than rewriting the source Run.
- Queued messages are durable submissions with editing, ordering, and consumption rules, not merely unsent browser text.

Reloading or reconnecting reconciles retained and live state. An observation gap cannot be repaired by assuming missing output was empty. The [execution guide](agents-and-runs.md) and [stream guide](streams-and-events.md) explain these boundaries independently of a particular screen layout.

One-time API credentials are displayed only at creation. Save them in an appropriate secret store; list/detail views do not recover the original value. Console does not contain embedded deployment credentials or demo data.

## Current limits

Usage and Schedules are marked coming soon. Console does not provide Asset management or Plugin, Secret, or Hook editors. Editing supported fields preserves existing hidden configuration; the absence of an editor does not mean the corresponding Service field is absent.

Traces offers only the configured backend's supported search targets and distinguishes disabled querying from temporary backend failures. Detail loads a root plus paginated observations, merges the root by ID, and preserves missing parents until their pages arrive. Root metrics are not trace totals; status and severity are separate. Full/compact selection controls retained content and diagnostic attributes, while the Run link provides authoritative execution outcome.

Trace querying requires query-backend configuration and permission to read the owning Run and its traces. Default Service composition supplies the Run/IAM authorizer. A configured exporter or reachable Langfuse UI is insufficient. MCP OAuth also has [callback requirements](identity.md#browser-oauth-callbacks): the provider returns to Console, which completes the attempt through the authenticated Service API as the initiating principal. A provider redirect alone cannot bypass state, callback-allowlist, authority, expiry, or replay validation.

## Hosting and development

Console is a private build input under `frontend/apps/a13n-console`, not a separately published SDK or a service-process role. Production hosting must serve its application shell for browser routes and proxy `/api` to Service on the same origin, including WebSocket upgrades. Configure Service's public origin to match. Static hosting and Service deployment are separate responsibilities.

The frontend uses shared components/tokens from `frontend/packages/a13n-ui` and the TypeScript SDK; neither establishes permission independently. Build and validation commands are documented in the [Console README](https://github.com/converge-ai-labs/agent-foundation/tree/main/frontend/apps/a13n-console). Frontend source changes are not required to use the deployed Console.

## Connect a GitHub bot

Open **Integrations → Bots → Connect bot → Create a new account** and choose a GitHub connection:

- **GitHub account · Polling** uses a dedicated ordinary account and a classic PAT. Use `notifications` plus `public_repo` for public repository comments, or `repo` for private repository work. Organization policy may require SSO authorization. The wizard identifies the account automatically. No public callback is needed. Notifications can combine updates, so the Agent reads the current Issue/PR before acting. Mention or subscribe the account to discussions in the repositories you configure; repository access alone does not guarantee notifications.
- **GitHub App · Webhook** uses your own installed App, its private key, and a webhook secret. Grant Issues and Pull requests read/write permissions and subscribe to Issues, Issue comments, Pull requests, Pull request reviews, and Pull request review comments. Supply the App/installation/owner/Bot IDs and verify them in the next step. Configure the displayed public event URL in GitHub; `connectivity.public_origin` supplies the deployment origin. GitHub must be able to reach it.

Choose one pilot repository, an Agent, and its execution Service Account. Select allowed sender usernames or `*`; explicit sender lists ignore notifications whose actor cannot be determined. Webhook setup also offers an event filter. **Verify and enable reception** verifies the current identity and repository before enabling the pilot. Prepare a test, post its text as an Issue/PR comment, and inspect the received, accepted, and reply observations. For polling, use a real mention of the connected account and allow at least one polling interval (60 seconds by default). Credentials stay in encrypted Account storage and are not copied into the Agent's shell environment.

The Bot's **Repositories** tab manages additional exact repositories; **Conversations** opens authorized execution history. Comments use the connected identity. GitHub Bot Memory, inline review replies, code changes, and PR creation are not included in the four built-in GitHub actions. Other configured Agent tools retain their own permissions.

For polling, enable **On GitHub** alongside **Email** for **Participating, @mentions and custom** in the connected account's [notification settings](https://github.com/settings/notifications). Enable the corresponding channel for **Watching** if you also want repository watch notifications. See GitHub's [notification delivery requirements](https://docs.github.com/en/subscriptions-and-notifications/get-started/configuring-notifications). A successful credential check only confirms API access; it does not verify these delivery settings. If comments exist but the notifications API returns no matching thread, check these settings and have another account send a new mention after enabling them.

If polling fails, inspect its last scan error on the overview and verify the PAT, account identity, permissions, and network access. Failed scans retain their cursor for retry. Polling does not mark notifications read. GitHub does not automatically redeliver failed webhooks: inspect its delivery log and explicitly redeliver after fixing the error. Neither path automatically retries a comment whose external outcome is unknown.
