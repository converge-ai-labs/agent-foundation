# Use Console

Console is the browser application for Service. It manages Workspace resources and conversations through the TypeScript Service SDK. It is not the optional Harness UI browser server and does not execute Agents in the browser.

## Open and sign in

Open the deployment's Console origin and use `/login`. The underlying `/api/v1/auth/login` is a JSON POST endpoint, not a page. Initial administrator invitation, password recovery, and email confirmation use the links issued by Service. The browser routes include `/forgot-password`, `/reset-password`, `/confirm-email`, and invitation acceptance.

For repository development, `make dev` starts Service and Console with the local development configuration. Console normally listens at `http://127.0.0.1:5173`, proxying `/api` HTTP and WebSocket traffic to Service on port 8000. `A13N_CONSOLE_SERVICE_URL` changes that upstream. Follow the [local development guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/dev/service/README.md) for dependency startup, fictional seed data, reset behavior, and shutdown; do not use seeded development identities in production.

Use the account menu to select English or Simplified Chinese. Workspace navigation uses Organization and Workspace keys, for example `/:organizationKey/:workspaceKey/agents`. Renaming a display label does not change its key; changing a key invalidates old readable addresses without redirect aliases.

## Configure before running

1. Choose the intended Organization and Workspace.
2. Add an accessible Model Provider, supply its credentials, and create an enabled Model. Provider discovery and tests can make remote requests.
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
