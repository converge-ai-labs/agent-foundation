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
| A specific Run       | `sessions/:sessionId/threads/:threadId/runs/:runId`       | Output, waiting feedback, control actions, execution detail                 |
| Models               | `models`                                                  | Models and configured Providers                                             |
| Search               | `settings?section=providers&category=search`              | Provider accounts, tests, and references                                    |
| Skills               | `skills`, `skills/:skillId`                               | Uploads, resources, and immutable revisions                                 |
| Environments         | `environments`, `environments/instances`                  | Templates, Provider configuration, actual runtime targets                   |
| Application Accounts | `application-accounts`, `application-accounts/:accountId` | Provider reception and object-specific routing                              |
| Connections          | `connections`                                             | Connected accounts and remote MCP servers; unified search and authorization |
| Traces               | `traces`, `traces/:traceId`                               | Authorized backend diagnostics or an explicit unavailable state             |
| Workspace settings   | `settings`                                                | Membership, invitations, keys, service accounts, permissions, and settings  |

Provider management lives in Workspace or Organization settings under `section=providers`, with `category=connectors` for Composio. The Connections dialog combines Provider applications, the deployment-resolved Remote MCP catalog from Service, and a custom URL; saved accounts and servers retain their separate backend resources. Operators can extend or override the catalog through Service configuration without rebuilding Console.

Organization settings live at `/organization/settings`; personal settings live at `/settings/profile`. Profile images, active sessions, password/email changes, and security activity belong to their identity settings, not Agent configuration.

## Set up and manage memory

1. Open Workspace settings → Providers → Memory. Organization administrators can instead create a shared Provider in Organization settings.
2. Choose an installed backend type and fill its configuration and write-only credential fields. Saving does not test connectivity. Storage configuration cannot change after creation; create another Provider for another storage target.
3. Open an Agent's Memory section, select the Provider, and choose the recall scope and behavior. Recall settings contain the result limit, optional similarity threshold, timeout, and required-recall option. Save the Agent revision to enable it. Off leaves stored records intact.
4. Use **View agent memories**, **Thread memories** in a Session, or **Memories** in the Workspace sidebar. Contextual links open a new tab so unsaved Agent configuration and the conversation stay open.
5. Select the Provider and scope, then choose **View memories**. **My memories** means your signed-in identity in this Workspace. An Agent uses its stable Agent ID; a Thread uses its stable Thread ID, even while you inspect an older Run. The loaded target remains identified while you adjust selectors.

The **Memories** sidebar entry, Session shortcut, and an unconfigured Agent's Memory section stay hidden until a Memory Provider is configured. Inherited Organization Providers count too. Disabled Providers remain discoverable for diagnosis and recovery, but cannot be selected for new Agent bindings. Configure the first backend under **Settings → Providers → Memory**; that setup category remains available. Existing Agent selections remain visible if their Provider becomes unavailable, and direct content links remain usable for recovery. Catalog errors do not mean that no backend exists.

The list shows loaded records, 20 per local page. A bounded backend response is not a total count or proof that an empty collection is exhaustive. **Load more records** appears only when the backend provides a continuation cursor. Semantic search runs independently against the backend and displays ranked matches; it is not a filter of the loaded page.

Open a record to inspect or edit it. Add and edit preserve text exactly, up to 8,000 characters. Delete requires confirmation. Read-only users can inspect but do not receive write controls. Provider administration does not grant content access; the Service checks the actual subject, including direct Agent grants and a Thread's current Run.

If a write cannot be confirmed, do not submit it again blindly. The dialog keeps the draft and offers **Inspect current state**. For adds, it shows semantic matches; a missing match does not prove that the add failed. Explicit acknowledgement permits another attempt, which may still create a duplicate. Provider creation has a similar saved-collection reconciliation flow, without exposing or comparing credentials.

Changing a Provider selection does not migrate old records. Select the old Provider on the Memories page to inspect them while it remains enabled and eligible. There is no automatic conversation extraction, bulk deletion, history, or memory export. See [long-term memory](memory.md) for backend setup and API semantics.

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
