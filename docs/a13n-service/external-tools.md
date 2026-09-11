# External tools

a13n Service selects external tools by managed ConnectorConnection or MCPConnection. Create the connection resource in the same Workspace before selecting it in an Agent configuration. Its ConnectorProvider may belong to that Workspace or its parent Organization. Organization Providers are automatically available to child Workspaces, while connections and authorized external accounts remain isolated by Workspace.

```json
{
  "connector_tools": [
    {
      "connector_connection_id": "cconn_1234567890abcdef",
      "tools": ["search"],
      "defer_loading": false
    }
  ],
  "mcp_tools": [
    {
      "mcp_connection_id": "mcpc_1234567890abcdef",
      "defer_loading": true
    }
  ]
}
```

Tool names in `tools` are exact source names. Omit `tools` or use `null` to allow all tools from that source. Use `[]` to allow none. A connection can appear only once in each list. Source aliases, inline URLs or credentials, and `exposure` are rejected.

`defer_loading` defaults to `false`, which makes selected definitions immediately visible. With `true`, the Harness exposes the group through its built-in `load_capability` tool. This changes model visibility; discovery and authorization still run during preparation.

A Run override inherits an omitted `connector_tools` or `mcp_tools` list. A supplied list replaces that entire category, and `[]` clears it. A null category is invalid. For example, `{"mcp_tools": []}` removes remote MCP selections while inheriting Connector selections.

## Connector Providers: Composio

Create a Provider under `/api/v1/workspaces/{workspace}/connector-providers` or the parent Organization, supply its service credentials, discover its Connectors, and create a Workspace ConnectorConnection. Complete external authorization before selecting that connection in `connector_tools`. Connector connections support the same tool selection and deferred-loading fields shown above.

Add a Composio Provider with a name and its write-only project `api_key`. Provider configuration is `{}`; Service uses the official Composio endpoint. All available applications appear in **Connections → New connection**; choose the Provider instance when several offer the same application. Service caches the directory for search and paging. Refresh replaces the complete directory only after a successful read; replacing the Provider key invalidates it.

Managed OAuth is selected by default when the application supports it. Service creates its shared auth configuration only when you connect, and reuses an existing configuration for later accounts. You can instead select an enabled custom OAuth2 configuration created in [Composio Dashboard](https://dashboard.composio.dev). Configure client IDs, client secrets and application scopes there. Service asks only for ordinary connection parameters; unsupported authentication displays its reason before setup.

If managed configuration creation has an uncertain result, Service does not send another creation request. Check Composio Dashboard and ensure an enabled OAuth2 configuration exists before retrying. This configuration is shared within the Provider; each Connection still authorizes its own Workspace-bound external account.

Set a stable `A13N_SERVICE_CONNECTIVITY_SETUP_CORRELATION_SECRET` of at least 32 bytes, shared by replicas and preserved across restarts. Configure `A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN` and the IAM public origin to the same Console/API ingress origin. In Composio project **Settings → General → Configuration**, set the identity verifier URL to `https://<your-public-origin>/connector-setup/callback`. Local OAuth testing requires a public HTTPS tunnel; open Console through that tunnel before signing in and starting authorization. Start from Console, not the Composio dashboard. See [Composio callback identity verification](https://docs.composio.dev/reference/api-reference/connected-accounts).

Complete Composio authorization in the same browser tab. Console retains an expiring browser proof in tab storage and posts the returned session to `/api/v1/connector-setup/complete` with the authenticated User and CSRF proof. The reverse proxy serves `/connector-setup/callback` from Console, routes `/api` to Service, and must omit callback query strings from access logs. If the tab context or login expires, sign in and start again; copying the authorization URL to another browser cannot complete the original connection.

An uncertain initial link request is not automatically repeated. A lost completion response is reconciled by reading the exact upstream account, never by replaying the single-use session. Check connection status before starting another authorization. If the authorization URL was lost, explicitly restart the unbound connection setup. An already verified Composio account cannot use this adapter's reconnect operation; create a new Connection instead of replacing its account identity.

When upgrading from the old callback protocol, stop old Control/all setup senders, apply the additive migration, deploy Console and Service together, and set the new verifier URL before opening setup again. The reconciler invalidates old incomplete Composio attempts without browser proof; ready Connections remain intact. Do not roll old senders back into the new flow. The old digest column remains unused for schema compatibility. Remote orphaned accounts are not automatically deleted.

Preview tool definitions before linking an account with `GET /api/v1/connector-providers/{provider_id}/connectors/{connector_key}/tools`. Preview does not grant execution access. A verified Connection keeps its own account binding, so clearing completed setup history does not break tool calls or re-enabling a disabled connection.

## Remote MCP connections

Service manages remote MCP endpoints, not Harness UI's local stdio server files. Configure endpoint policy and credential encryption before creating a connection. This example uses an HTTPS server that deliberately needs no authentication; replace the endpoint with your authorized server and choose the authentication mode it requires.

Save as `mcp-connection.json`:

```json
{
  "name": "Documentation tools",
  "endpoint_url": "https://mcp.example.com/mcp",
  "auth_mode": "none"
}
```

```bash
curl --fail-with-body "$SERVICE_URL/api/v1/workspaces/$WORKSPACE/mcp-connections" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: docs-create-mcp-001' \
  --data-binary @mcp-connection.json
```

Save the returned connection `id` and `version`. Creation is not proof that the endpoint is reachable or its tools are usable. The authentication modes are `none`, `bearer`, `static_headers`, and `oauth`:

| Mode             | Next step                                                                                                                                         |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `none`           | Discover using the current resource version                                                                                                       |
| `bearer`         | POST write-only `bearer` plus `expected_version` to `/mcp-connections/{connection_id}/credentials` with an idempotency key                        |
| `static_headers` | Declare up to 16 `static_header_names` at creation; replace credentials with matching `static_headers` values and current version                 |
| `oauth`          | POST current `expected_version` to `/mcp-connections/{connection_id}/authorize` with an idempotency key; follow its expiring authorization launch |

All table paths are under `/api/v1`. Credential replacement does not expose stored values in reads; keep source credentials outside committed JSON files. OAuth has [browser callback constraints](identity.md#browser-oauth-callbacks): a provider redirect alone does not satisfy local session/CSRF checks.

To discover tools, POST `{"expected_version": VERSION_FROM_READ}` to `/api/v1/mcp-connections/{connection_id}/discover`. This performs remote work. Use returned source-native names in the Agent's `mcp_tools` selection, not generated model-facing aliases. The returned collection contains tool names, descriptions, input/output schemas, and annotations.

Resource states are `pending`, `ready`, `action_required`, and `disabled`; `action_required` includes its reason, such as reauthorization or incompatibility. PATCH updates the supported display name with an expected version, not arbitrary endpoint/auth fields. Reconnect, enable, disable, and delete are separate versioned commands. Delete returns cleanup evidence; it is not proof that remote revocation succeeded. Read the [Native operation reference](api-reference.md#connectivity-management) for each command's body and header requirements.

Remote MCP presets prefill documented endpoints and authentication modes; they do not certify a completed authorization flow. Asana requires a pre-registered OAuth client and Vercel's resource address is incompatible with the current endpoint normalization, so their presets explain the limitation before creation. OAuth servers must support dynamic client registration or client metadata documents and return the authorization response issuer (`iss`) required by Service's issuer validation.

## Application Accounts and event reception

An Application Account represents one provider account, Bot, or concrete application installation. Configure its credentials through `/api/v1/workspaces/{workspace}/application-accounts`. Credentials are write-only and encrypted on the Account.

Reception is disabled by default. To receive events, set `receive_enabled: true`, `default_agent_id`, and a same-Workspace `execution_service_account_id` on the Account. Configure the provider webhook at `/connectivity/v1/accounts/{account_id}/events`. A tool-only Account needs neither an Agent nor an execution Service Account.

Use `/api/v1/application-accounts/{account_id}/targets` to configure an exact Slack channel, Lark chat, or GitHub repository ID. Targets can override the Agent and only `model`, `skills`, `connector_tools`, and `mcp_tools`. Omitted categories inherit and empty lists clear. Optional batching and provider policy inherit Account defaults. Target updates replace the full configuration; disabled targets do not fall through to defaults.

The first event is submitted immediately; subsequent ordered batches obey the configured interval. An active or selected waiting Run receives Steer without changing its tools. The next ordinary Run uses the current Agent and override while retaining the same Thread. Closing reception stops new admission while already acknowledged batches continue processing. Disabling or deleting the Account also blocks pending execution and subsequent outbound calls.

Account tools are injected automatically from the Run's trusted execution context. There is no Account selection in Agent configuration or Run overrides. Inbound admission supplies only its admitted reply actions and target. A trusted independent entry can supply explicit proactive actions and destinations. With no such context, the Run gets no Account tools; creating an Account alone does not expose it to every Agent. Clearing `connector_tools` or `mcp_tools` does not remove these default tools.

For proactive sends, Slack accepts `channel_id` and `text` within the entry-authorized `channel_ids`; Lark accepts `chat_id` and typed `content` within `chat_ids`. GitHub scope contains exact repositories, and its tools accept a permitted repository ID, issue/PR number, and target kind. Tools cannot choose another Account or credential. Proactive sends do not automatically create an external Thread binding.

## Discovery and execution

Acceptance validates managed resources and permissions without contacting external servers. The executing Worker discovers the selected sources. A missing explicitly selected tool fails preparation. Tool schemas can change between Attempts; acceptance does not retain schema snapshots. Use explicit names to limit which tools a source can contribute.

A subagent uses its own selected Connector and MCP tools. Parent Run acceptance freezes the complete child graph. With `subagent_mode: "inline"`, each child's tools live in the parent Attempt; with `subagent_mode: "async"`, its independently scheduled Run prepares them. Children receive no parent native ingress context. Attempt cleanup closes all prepared tool sessions, including when preparation only partly succeeds.

Every connection receives a separate capability and stable, system-generated tool names. Names are normalized and shortened for model APIs; the source-native name remains the value used in `tools` selections and provider calls. Two accounts at the same remote URL remain independent. Connector tools call source-bound local Toolsets through the Harness MCP capability. Remote MCP uses the upstream client directly from the Worker. Credentials are resolved from current managed state before outbound requests, and each dispatch checks the current Attempt and resource authority.

Inbound Slack, Lark, and GitHub reply actions use the protected target admitted with the input event. The model supplies action content, not a destination, connection, or credential. Receive-only input contributes no native tools. Replies preserve the admitted conversation binding. An uncertain write remains an unknown outcome and is not automatically repeated.

Discovery is bounded per source to 128 pages, 2,048 tools, and 16 MiB of tool definitions. Each result is bounded to 1 MiB. Invalid schemas, external schema references, excessive nesting, and oversized definitions or results fail explicitly.

## Host integration

Worker composition provides `WorkerRuntime.external_tools`. A host constructing `HarnessDriver` passes this collaborator as `external_tools`; the driver creates and closes fresh capabilities around each Attempt's Harness execution. Admission integrations use `connectivity.native_context.InboundRunContext.from_batch()` and retain its JSON as one entry in the accepted Run's `native_tool_contexts`. For proactive operations, a trusted entry calls `bind_account_tools()` inside its short acceptance transaction with the execution actor, Workspace, exact Account, allowed actions, and authorized target scope, then persists the returned context in that tuple. The entry owns target authorization; the helper checks Account use and scope validity. Neither context comes directly from public input or a model argument.

Replacement Attempts and inherited continuations retain these contexts and resolve fresh credentials. New child Runs receive no parent native contexts by default. The Harness sees ordinary MCP capabilities and needs no Account-specific configuration.

Control owns Account and connection management, setup, and short-lived OAuth state expiration. OAuth refresh is demand-driven before authenticated use; interrupted exchanges require new authorization. Connection deletion first invalidates locally, then makes one bounded remote cleanup attempt. Failed or unknown cleanup is reported honestly and has no background retry. Connectivity owns inbound delivery and admission. Workers own tool discovery and outbound execution. No local MCP listener or separate MCP service is needed. `A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN` is required for interactive callback flows, not for noninteractive management. Remote MCP must negotiate protocol `2025-11-25`; authenticated MCP endpoint redirects are rejected.

## Development database setup

The initial migrations create the current schema directly, without persisted tool catalogs or Run tool snapshots. If a local database was created from the previous schema, recreate it and reauthor Agent configurations with connection-selection lists. Existing revision stamps cannot update rewritten initial migrations. Skill, Plugin, and Environment locks keep their existing contracts.

## Retrying management commands

Use an `Idempotency-Key` containing 1–512 visible ASCII bytes for retryable management commands. If a response is lost, repeat the same key and request. For 24 hours from the original successful commit, an authorized replay returns the original accepted result, even if the resource has since advanced. Changing the request while reusing that key returns a conflict. Replay does not renew the window.

Read an MCP connection after a mutation to observe current discovery status; the mutation receipt records its accepted state. Supply credentials through the owning Account, Provider, or MCP connection. Run configuration selects those managed resources and does not accept direct credential overrides.
