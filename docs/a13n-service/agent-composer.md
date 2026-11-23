---
title: Agent Composer
description: Create, test, and tune agents using natural language conversation, secure credential entry cards, and platform MCP tools.
---

**Agent Composer** is Service's built-in conversational agent builder. Instead of writing complex JSON specifications manually, you describe the agent's responsibilities, required tools, and guidelines in plain language. Composer inspects your workspace's available models and tools, drafts a complete configuration proposal, and asks for your approval before persisting changes as an immutable revision.

Composer also configures providers and schedules, analyzes recorded model usage and costs, manages Webhooks and environment egress credentials, and diagnoses executions.

## Start Agent Composer

To launch Composer, you need workspace `write` permission and at least one configured, enabled model.

1. In Console, navigate to **Agents → Create with AI**.
2. Type your requirement, for example:
   > *"Create an agent that reviews GitHub pull requests for security vulnerabilities. It should check dependency manifests, verify input validation, and flag missing authentication checks. Use an available fast model and show me the instructions before saving."*
3. Review the proposed agent configuration displayed in the interactive proposal card.
4. Approve the write operation. Once confirmed, Composer persists the configuration as an immutable initial revision.
5. Click **Try agent** to open a test chat thread, send sample inputs, and verify that the agent behaves as expected.
6. To make adjustments later, open the agent and select **Edit with AI**. Composer loads the active revision, understands existing settings, and applies modifications safely while preserving untouched fields.

## Configure a provider

When an agent needs a new model provider, external MCP connection, or cloud environment, Composer guides you through setup without exposing secrets:

1. Ask Composer to configure a provider (e.g., *"Connect an Anthropic account for high-reasoning tasks"*).
2. Composer presents a **Private Provider Card** directly in the chat UI.
3. Enter your sensitive API key or secret token inside the card and click **Save provider**.
4. The card encrypts and transmits your credentials directly to the Service provider management endpoint. **Credentials never enter LLM prompt contexts, tool arguments, or conversation history.**
5. Once the credential is saved, Composer receives the newly registered provider ID and completes the remaining agent configuration.

## Analyze usage and costs

Open **Usage**, select a time range and any member or Agent filter, then choose **Ask Composer**. The conversation starts with those exact filters and the browser timezone. Ask for totals, model or Agent breakdowns, expensive Sessions, context usage or request latency. Member statistics require workspace admin. Composer reports recorded model costs in USD and the number of requests without pricing; these are known costs, not an invoice or a total including environment costs.

## Configure Webhooks and credentials

Choose **Ask Composer** from **Settings → Webhooks** or **Credentials**, including their detail pages. Webhook reads and writes require workspace admin. Credential configuration requires write permission; using the conversation also requires run permission.

- Webhooks: inspect subscriptions and deliveries, change event filters or endpoints, enable/disable, delete, and retry dead deliveries. Creation and signing-key replacement use a private card. On creation, save the displayed signing key in your receiver, then select **I saved the secret**. Verify `webhook-signature` and deduplicate by `webhook-id`. Queued deliveries retain their original endpoint and key.
- Environment credentials: create, edit metadata, rotate, revoke and request synchronization through a private card. Allowed domains stay fixed after creation. Credentials managed by an App must be changed through that App. Revocation in Service is permanent and leaves upstream token revocation to its provider.

Tokens and signing keys go directly from the card to the existing Service APIs. Only status, resource IDs and versions return to Composer. After saving a credential, check **Session application**: active Sessions update asynchronously and idle Sessions update on next use. A sync request is a request to apply the value, not proof that every Session has applied it.

A conflict requires a fresh read and proposal. If a save result is unknown, check the resource page before requesting another write. Submitting or retrying the response after a confirmed save does not repeat the action in that open card; after reloading, reconcile the resource before repeating a create.

## What it can do

Composer operates through authenticated platform MCP tools scoped to your workspace permissions:

| Resource domain             | Operations performed by Composer                                                                    |
| :-------------------------- | :-------------------------------------------------------------------------------------------------- |
| **Agents**                  | Create, inspect, rename, label, and publish agent revisions; set active defaults                    |
| **Models**                  | Discover existing models, register new model providers, and tune temperature or token budgets       |
| **Connections**             | Discover and test remote MCP servers, configure tool selections, and verify health                  |
| **Skills**                  | Import and validate public GitHub skill repositories or uploaded skill packages                     |
| **Environment templates**   | Create and configure Docker or sandbox container templates for isolated code execution              |
| **Schedules**               | Draft cron timetables, configure time zones, preview upcoming execution instants, and test triggers |
| **Diagnostics & Execution** | Query session threads, inspect raw run attempts, analyze error stack traces, and record findings    |

All write operations (creating agents, updating models, saving schedules) require explicit human approval cards before changes are written to the database.

## Prepare through the API

Developers can invoke Composer programmatically via the Service HTTP API using a workspace API key:

```bash
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -o composer.json

jq '{id, source, preset_kind, default_revision_id}' composer.json
```

The response returns an agent object with `preset_kind: "composer"` and an assigned `id`. You can then dispatch messages to this agent using the standard `/api/v1/sessions` API to build agents via automated pipelines.

## Choose its model

When initializing Composer for the first time, Service selects an optimal model from your workspace:

1. It searches configured models against the deployment's preference list (e.g., modern flagship reasoning models).
2. If none match, it selects the first enabled model available in the workspace.

If no models are currently configured in the workspace, initialization returns a `409 conflict` indicating that a model must be added first.

## Built-in agent rules

Composer is implemented as a versioned built-in agent. You can customize its operating parameters:

- Navigate to Composer's Agent settings page to adjust reasoning effort, temperature, or maximum token limits.
- To give everyone in the workspace a default environment, save a shared instance or a template in Composer’s settings. A thread without a primary mount uses this default. Composer can read project files, including `AGENTS.md`. Choose **No primary environment** in Run options to skip it for one run.
- Add **Supplemental Instructions** in the configuration editor to enforce organization-wide standards (e.g., *"Always enforce snake_case for tool identifiers"*).
- Supplemental rules are merged with official system guidance at execution time.
- To maintain an auditable environment, you can duplicate Composer into an entirely custom workspace agent if you need unrestricted prompt modifications.

## Use the toolset in your own agents

The platform MCP tools that power Composer can be attached to your own custom agents, allowing you to build specialized internal meta-agents (e.g., self-healing monitoring bots or workspace auditors):

1. Open your agent's configuration and expand **Advanced / Platform Features**.
2. Enable the required toolsets (such as `agents`, `models`, `schedules`, or `execution`).
3. Attach the built-in `platform-operations` skill to provide the agent with standard operational guidelines.
4. Enable basic file viewing tools (`view`, `ls`, `glob`) so the agent can read operational schemas:

```json title="agent_config.json"
{
  "toolsets": {
    "agents": {
      "enabled": true,
      "tools": {
        "archive_agent": { "enabled": false }
      }
    },
    "models": { "enabled": true },
    "execution": { "enabled": true }
  },
  "builtin_skills": ["platform-operations"]
}
```

Calls to platform tools execute within the security boundary of the active caller, ensuring strict multi-tenant isolation.

### Computer coordinate mode

When your custom agent includes desktop automation (Computer Use) alongside platform operations, configure whether coordinates use normalized percentages or raw pixel dimensions in your environment settings.

## Built-in preparation behavior

Before the first submission of a new unpinned Composer conversation, Console prepares the preset when the current user has write permission. An updated definition creates a candidate revision while preserving the selected default, model settings and supplemental instructions. Select the new revision explicitly to use its new tools. Existing conversations and pending-call resume retain their accepted configuration.

Deploy Control, Worker and Console together before preparing the new definition. The `usage`, `subscriptions` and `egress_credentials` toolsets are enabled in the new Composer candidate and disabled by default for ordinary Agents. No database migration or data backfill is needed; older binaries may not recognize the new toolset keys.
