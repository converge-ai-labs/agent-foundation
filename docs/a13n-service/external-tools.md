# External tools

A Connection is a Workspace resource for an authorized external account or a remote MCP server. Create it before selecting its tools in an Agent. Its immutable `source` identifies either a Connector Provider and application, or an MCP endpoint and authentication mode. A Connector Provider can belong to the Workspace or its parent Organization; Connections remain isolated by Workspace.

```json
{
  "connection_tools": [
    {
      "connection_id": "conn_1234567890abcdef",
      "tools": ["search"],
      "permission": "inherit",
      "defer_loading": false
    },
    {
      "connection_id": "conn_fedcba0987654321",
      "permission": "inherit",
      "defer_loading": true
    }
  ]
}
```

Tool names are exact source names. Omit `tools` or use `null` to select all authorized tools from that source; `[]` selects none. `permission` defaults to persisted `inherit`, and `permissions` can override exact selected tool names. An effective `review` consults a matching configured reviewer; without one, the review layer adds no restriction. Each Connection can appear only once. Run overrides inherit an omitted `connection_tools` list; a supplied list replaces the whole selection, and `[]` clears it. A null list, aliases, inline endpoints, and credentials are rejected.

`defer_loading` defaults to `false`, which makes selected definitions immediately visible. With `true`, the Harness exposes the group through `load_capability`. Discovery and authorization still run during preparation. Tools execute only inside an accepted Agent Run; there is no standalone connection execute endpoint.

Run acceptance freezes each selected Connection's authorization generation. Reauthorizing the same Connection, even as a different upstream account, keeps its ID and advances that generation. Earlier Runs cannot use the replacement authorization. Routine OAuth token refresh preserves the generation.

## Create and authorize a Connection

Create either source with `POST /api/v1/workspaces/{workspace}/connections` and an `Idempotency-Key`:

```json
{
  "name": "Work GitHub",
  "source": {
    "kind": "connector",
    "provider_id": "cnr_1234567890abcdef",
    "connector_key": "github"
  }
}
```

```json
{
  "name": "Research tools",
  "source": {
    "kind": "mcp",
    "endpoint_url": "https://tools.example/mcp",
    "auth_mode": "oauth"
  }
}
```

Creation is local and returns a pending Connection. Read or rename it through `/api/v1/connections/{connection_id}`. Enable, disable, check, and delete use the same resource for both source kinds. Enabling permits verification; it does not prove remote eligibility.

Create an Authorization with `POST /api/v1/connections/{connection_id}/authorizations`, an `Idempotency-Key`, and the Connection's `expected_version`. The returned operation has its own ID, status, expiry, safe error code, and `next_action`. Query it at `/api/v1/connection-authorizations/{authorization_id}`. A completed authorization may still require an explicit `POST /api/v1/connections/{connection_id}/check`; inspect the Connection's status and `last_check`. A failed or unavailable check reports what was checked without promising that every upstream action will succeed.

### Application-owned users

Your application owns its customer identities and maps them to Connection IDs. Service does not require an a13n User for each customer. Your backend can use a Service Account API key with Workspace Builder authority to create Connections and authorize them. Keep that API key in the backend.

Connector browser authorization uses Service's browser handoff:

1. Register the exact HTTPS application callback in `A13N_SERVICE_CONNECTIVITY_AUTHORIZATION_CALLBACK_URLS`. Configure a public HTTPS `A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN` for the Service browser handoff.
2. Generate unpredictable application `state` and a completion verifier. Retain them in the initiating application session. Send `method: "browser"`, `return_url`, `state`, `completion_challenge` (the lowercase hexadecimal SHA-256 digest of the verifier), `expected_version`, and source-specific `options`.
3. Open the returned `next_action.url` in the customer's browser. Service binds the link to that tab and handles the provider flow.
4. At your callback, remove the query parameters from browser navigation state, validate `state` and `authorization_id`, and send `receipt` plus `completion_verifier` from the same authenticated backend principal to `POST /api/v1/connection-authorizations/{authorization_id}/complete`.
5. Query the operation and Connection after an uncertain response. Start a new authorization only when needed. Customers do not log in to Console.

Serve callbacks without caching or referrer forwarding, and exclude callback query strings from access logs. Route `/connection-authorizations/browser` and its script to Service and `/api` to Service. The application callback can be hosted separately.

Remote MCP OAuth returns directly to the application instead:

1. Add the exact application callback to `A13N_SERVICE_CONNECTIVITY_AUTHORIZATION_CALLBACK_URLS`. Use HTTPS, except for exact loopback HTTP during local development.
2. Call `POST /api/v1/connections/{connection_id}/mcp/oauth-setup` with `{"redirect_uri":"https://app.example/oauth/callback"}`. Follow its typed `next_action`: configure a client, start authorization, authenticate with client credentials, check the Connection, or finish.
3. For `start_authorization`, create a browser Authorization with `method: "browser"`, the Connection `expected_version`, and the same `redirect_uri`. Retain the returned authorization ID and the unpredictable `state` from `next_action.url` in the initiating application session, then open that URL.
4. At the callback, remove the query from browser history before rendering or authenticating. Verify the returned state against the retained value. From the same authenticated principal that started the attempt, call `POST /api/v1/connection-authorizations/{authorization_id}/complete` with `state`, exactly one of `code` or provider `error`, and `iss` when supplied.
5. Read the Authorization and Connection after an uncertain response. Never resubmit a possibly consumed code with a new attempt.

For example, a backend completion request is:

```http
POST /api/v1/connection-authorizations/authz_123/complete
Authorization: Bearer <application-service-account-key>
Content-Type: application/json

{"state":"<returned-state>","code":"<provider-code>","iss":"https://authorization.example"}
```

The application must not put its Service credential in browser code. Console performs the same completion through its authenticated, CSRF-protected client at `/connections/callback`. MCP OAuth has no public Service callback, receipt, completion verifier, or manual confirmation step.

### Direct credentials

For API key, bearer, or basic credentials, use `method: "credentials"` with the selected source's write-only `credentials` object and setup `options`. Connector discovery returns `credential_schemas` keyed by authentication method. For MCP bearer mode, credentials are `{"bearer": "..."}`. For MCP static headers, set `source.static_header_names` at creation and supply a complete map of those header names to values as `credentials`. Credentials never appear in Connection or Authorization reads.

## Connector Provider: Composio

Create a Provider under `/api/v1/workspaces/{workspace}/connector-providers` or the parent Organization with `type: "composio"`, `configuration: {}`, and its write-only project `api_key`. The Provider owns this project credential; each Connection owns one customer account binding. **Connections → New connection** lists available applications and Provider instances. Directory refresh publishes a complete snapshot only after a successful read; replacing the Provider key invalidates it.

Select an authentication configuration and the toolkit version from the application's setup schema. OAuth 2.0, API key, bearer token, and basic authentication are supported. Browser setup uses Composio's hosted form. API clients can supply non-OAuth credentials directly, along with required instance fields such as subdomain or region. Composio stores the account credential; Service does not retain the plaintext submission.

If no configuration exists, Service can create a reusable configuration for managed OAuth or an offered non-OAuth method that requires no application-level credentials. To use your own OAuth app or scopes, create the auth config in [Composio Dashboard](https://dashboard.composio.dev), configure its client credentials and redirect URI there, then refresh configurations in Console. If configuration creation has an uncertain outcome, inspect Composio before retrying; Service does not repeat an uncertain remote creation automatically.

Set a stable `A13N_SERVICE_CONNECTIVITY_SETUP_CORRELATION_SECRET` of at least 32 bytes, shared by replicas. Set Composio's **OAuth user verification** callback to `https://<service-public-origin>/connection-authorizations/browser`. Connection return URLs do not replace this project setting. Local browser testing requires a public HTTPS ingress.

Complete hosted authorization in the same tab. Service verifies the fixed upstream account, Provider, application, and correlation before binding it. A ready Connection means Composio accepted that account; some non-OAuth forms do not test credentials against the target service. The Connection check therefore reports `provider_account` scope. Later tool calls may report missing permissions or invalid credentials.

Reauthorization uses the existing Connection's `/authorizations` endpoint. Its ID remains stable when the upstream account changes. A lost completion response is reconciled by reading the exact account, never by resending a consumed provider session. Explicit Connector revocation is `/api/v1/connections/{connection_id}/connector/revoke`; deletion and revocation report local invalidation separately from remote cleanup success or uncertainty.

## Remote MCP

MCP sources support `none`, `bearer`, `static_headers`, and `oauth`. A `none` connection becomes ready through an explicit check. Other modes require credentials or OAuth before checking. MCP checks report `mcp_discovery` scope; they establish protocol and tool-discovery eligibility, not successful execution of every tool.

List the Service-owned setup catalog through `GET /api/v1/mcp-servers`; use `query`, `limit`, and `cursor` for search and pagination, or read one key through `GET /api/v1/mcp-servers/{server_key}`. Built-in and operator-configured entries are templates only. Create an ordinary Connection from the selected endpoint and authentication fields.

OAuth uses discovered authorization-server metadata. Automatic registration is preferred where supported. Request the next setup action through `/api/v1/connections/{connection_id}/mcp/oauth-setup`, manage a connection-owned OAuth app through `/api/v1/connections/{connection_id}/mcp/oauth-client`, and use `/mcp/oauth-discovery` only when the raw safe capability projection is needed. The app configuration contains its issuer, client ID, grant type, token authentication method, bound redirect URI, and write-only client secret. Replacing or removing it clears active tokens and requires authorization again. Failed authorization retains the app configuration for correction.

Authorization-code clients use the application-owned callback flow above. Machine clients use `method: "client_credentials"` at the common Authorization endpoint after their app configuration is saved. Refresh uses the current configured client and does not change the Connection's authorization generation.

## Application Accounts and event reception

An Application Account represents one provider account, Bot, or concrete application installation. Configure its credentials through `/api/v1/workspaces/{workspace}/application-accounts`. Credentials are write-only and encrypted on the Account.

Reception is disabled by default. To receive events, set `receive_enabled: true`, `default_agent_id`, and a same-Workspace `execution_service_account_id` on the Account. Configure the provider webhook at `/connectivity/v1/accounts/{account_id}/events`. A tool-only Account needs neither an Agent nor an execution Service Account.

Use `/api/v1/application-accounts/{account_id}/targets` to configure an exact Slack channel, Lark chat, or GitHub repository ID. Targets can override the Agent and only `model`, `skills`, `connection_tools`. Omitted categories inherit and empty lists clear. Optional batching and provider policy inherit Account defaults. Target updates replace the full configuration; disabled targets do not fall through to defaults.

The first event is submitted immediately; subsequent ordered batches obey the configured interval. An active or selected waiting Run receives Steer without changing its tools. The next ordinary Run uses the current Agent and override while retaining the same Thread. Closing reception stops new admission while already acknowledged batches continue processing. Disabling or deleting the Account also blocks pending execution and subsequent outbound calls.

Account tools are injected automatically from the Run's trusted execution context. There is no Account selection in Agent configuration or Run overrides. Inbound admission supplies only its admitted reply actions and target. A trusted independent entry can supply explicit proactive actions and destinations. With no such context, the Run gets no Account tools; creating an Account alone does not expose it to every Agent. Clearing `connection_tools` does not remove these default tools.

For proactive sends, Slack accepts `channel_id` and `text` within the entry-authorized `channel_ids`; Lark accepts `chat_id` and typed `content` within `chat_ids`. GitHub scope contains exact repositories, and its tools accept a permitted repository ID, issue/PR number, and target kind. Tools cannot choose another Account or credential. Proactive sends do not automatically create an external Thread binding.

## Discovery and execution

Acceptance validates managed resources and permissions without contacting external servers. The executing Worker discovers the selected sources. A missing explicitly selected tool fails preparation. Tool schemas can change between Attempts; acceptance does not retain schema snapshots. Use explicit names to limit which tools a source can contribute.

A subagent uses its own selected Connector and MCP tools. Parent Run acceptance freezes the complete child graph. With `subagent_mode: "inline"`, each child's tools live in the parent Attempt; with `subagent_mode: "async"`, its independently scheduled Run prepares them. Children receive no parent native ingress context. Attempt cleanup closes all prepared tool sessions, including when preparation only partly succeeds.

Every connection receives a separate capability. Its model-facing name is frozen from the Connection name when the Run is accepted, for example `conn_notion`; a collision adds a short suffix. Short, model-compatible source tool names remain readable under that prefix. Names requiring normalization or shortening receive a stable suffix. The source-native name remains the value used in `tools` selections and provider calls, and the Connection ID is the internal permission and routing identity. Two accounts at the same remote URL remain independent. Connector tools call source-bound local Toolsets through the Harness MCP capability. Remote MCP uses the upstream client directly from the Worker. Credentials are resolved from current managed state before outbound requests, and each dispatch checks the current Attempt and resource authority.

Inbound Slack, Lark, and GitHub reply actions use the protected target admitted with the input event. The model supplies action content, not a destination, connection, or credential. Receive-only input contributes no native tools. Replies preserve the admitted conversation binding. An uncertain write remains an unknown outcome and is not automatically repeated.

Discovery is bounded per source to 128 pages, 2,048 tools, and 16 MiB of tool definitions. Each result is bounded to 1 MiB. Invalid schemas, external schema references, excessive nesting, and oversized definitions or results fail explicitly.

## Host integration

Worker composition provides `WorkerRuntime.external_tools`. A host constructing `HarnessDriver` passes this collaborator as `external_tools`; the driver creates and closes fresh capabilities around each Attempt's Harness execution. Admission integrations use `connectivity.native_context.InboundRunContext.from_batch()` and retain its JSON as one entry in the accepted Run's `native_tool_contexts`. For proactive operations, a trusted entry calls `bind_account_tools()` inside its short acceptance transaction with the execution actor, Workspace, exact Account, allowed actions, and authorized target scope, then persists the returned context in that tuple. The entry owns target authorization; the helper checks Account use and scope validity. Neither context comes directly from public input or a model argument.

Replacement Attempts and inherited continuations retain these contexts and resolve fresh credentials. New child Runs receive no parent native contexts by default. The Harness sees ordinary MCP capabilities and needs no Account-specific configuration.

Control owns Account and connection management, setup, and short-lived OAuth state expiration. OAuth refresh is demand-driven before authenticated use; interrupted exchanges require new authorization. Connection deletion first invalidates locally, then makes one bounded remote cleanup attempt. Failed or unknown cleanup is reported honestly and has no background retry. Connectivity owns inbound delivery and admission. Workers own tool discovery and outbound execution. No local MCP listener or separate MCP service is needed. `A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN` is required for the Connector browser bridge; Remote MCP clients return directly to an allowlisted application callback. Remote MCP must negotiate protocol `2025-11-25`; authenticated MCP endpoint redirects are rejected.

## Development database setup

The initial migrations create the current schema directly, without persisted tool catalogs or Run tool snapshots. If a local database was created from the previous schema, recreate it and reauthor Agent configurations with connection-selection lists. Existing revision stamps cannot update rewritten initial migrations. Skill, Plugin, and Environment locks keep their existing contracts.

## Retrying management commands

Use an `Idempotency-Key` containing 1–512 visible ASCII bytes for retryable management commands. If a response is lost, repeat the same key and request. For 24 hours from the original successful commit, an authorized replay returns the original accepted result, even if the resource has since advanced. Changing the request while reusing that key returns a conflict. Replay does not renew the window.

Read the Connection after a mutation to observe current discovery status; the mutation receipt records its accepted state. Supply credentials through the owning Account, Provider, or MCP connection. Run configuration selects those managed resources and does not accept direct credential overrides.

## Scheduled tasks in Slack and Feishu

In an enabled Slack channel or Feishu/Lark group, mention the Bot with a scheduling request, for example: “Every weekday at 09:00 Asia/Shanghai, summarize decisions and open questions in this channel.” One-time reminders are also supported. Include a timezone; if the time is ambiguous, clarify it before confirming.

The Bot posts a confirmation card showing the task, schedule, timezone, and destination. Click **Confirm** to activate it. Only the original requester can confirm or manage the task. To inspect tasks, ask “What scheduled tasks are set up in this channel?” To change one, mention the Bot with its task ID and the new instructions or time, then confirm the updated card. The existing schedule remains active until the edit is confirmed. Cards also provide **Pause**, **Resume**, and **Delete**. Pausing or deleting does not cancel an already running occurrence; use that occurrence's **Stop task** button instead.

The service must be running. After downtime, missed intervals coalesce into one late execution rather than replaying a backlog. Resuming starts with the next future occurrence. Each occurrence posts a normal task card and its explicit result in the same channel. Execution status does not by itself prove delivery. Account, channel, or execution permission changes can pause the schedule; inspect the reported error and recreate the task under the new configuration. Confirmation expires after 24 hours. An uncertain initial card delivery is reported by task listing and is not automatically posted again.

This workflow supports Slack channels and Feishu/Lark group chats. Enable card action callbacks as well as message events for the Bot application; HTTP callbacks are authenticated before any task is changed. Feishu cards display Chinese when the Account brand is `feishu`. Direct messages do not expose scheduling tools. Calendar schedules include one-time, daily, weekly, and selected-weekday tasks; GitHub event follow-ups in Slack use the flow below.

## Event subscriptions

`bot_routines` manages both scheduled and event-triggered channel tasks. The Agent calls `event_sources` to discover authorized targets, each target's supported event types and filter schemas, and its setup requirements. It uses `propose` to create or edit a task, or propose pause, resume, or deletion; `list` returns existing tasks and their status. The requester confirms changes using the channel card. The service binds the destination, creator, and execution identity from the originating conversation; these are not model arguments.

An event definition chooses a discovered `source_target_id`, an advertised `event_type`, exact `filters` conforming to its schema, and `once`. Set `once: true` for one accepted occurrence, or `once: false` for ongoing monitoring. A definition must contain either `schedule` or `event`, never both. For example, the definition inside a `propose` request can be:

```json
{
  "title": "Notify when PR 42 merges",
  "prompt": "Summarize the merge and notify this channel with its link.",
  "event": {
    "source_target_id": "<source_target_id returned by event_sources>",
    "event_type": "github.pull_request.merged",
    "filters": {"pull_request_number": 42},
    "once": true
  }
}
```

The service waits for events; the Agent does not remain running. New platform support requires a trusted event-source adapter that handles its event catalog, validation, matching, and safe facts. The same persistence, confirmation, pause/resume, deduplication, and execution machinery is reused. GitHub App webhooks are the currently supported source; this interface does not make other platforms available automatically.

### GitHub event follow-ups in Slack

Mention the Bot with a request such as “When PR #42 merges, notify this channel” or “When GitHub Actions fails on this repository's main branch, post a summary here.” Include a repository or PR link. The Agent discovers configured sources and proposes a confirmation card showing the repository, condition, and destination. Confirm it to begin watching. Use `github.pull_request.merged` with a required `pull_request_number` and `once: true` for a merge notification. Use `github.workflow.failed` with optional `branch` and `workflow_id` filters and `once: false` to monitor CI failures continuously. Either event type supports one-time or ongoing execution. Ordinary PR closure, successful workflows, cancellation, and timeout do not match these conditions.

An administrator must first connect a GitHub App, configure the repository target, and assign the same Agent and execution Service Account used by the Slack channel. Verify the repository in Bot setup so its name is available for discovery. Enable the App's **Pull request** and/or **Workflow run** webhook subscriptions, with the required repository permissions (**Actions: read** for workflow events), and configure its public a13n event URL. Notification polling accounts do not support this workflow. Events are watched by Service, not by a long-running Agent or repeated model queries. When an event matches, a normal Agent Run executes the confirmed instructions and posts through the channel's reply tool.

Ask the Bot to list tasks, change instructions, pause, resume, or delete a subscription. Only its creator can confirm or manage it. Confirmed edits and resumption start watching from that point; paused or historical events are not replayed. Duplicate webhooks do not create duplicate task executions. Distinct CI attempts queue behind an active task. Account, repository, or execution-permission changes can block execution; recreate the subscription under the intended configuration. The notification shares repository information into the original channel, so verify that destination before confirming.

Slack channel interactions are supported in this version. Feishu shares the Service design but its event-task confirmation and delivery interaction is not exposed yet. This feature does not implement arbitrary GitHub checks, polling fallback, or direct-message destinations. Run completion and successful Slack delivery remain separate outcomes.
