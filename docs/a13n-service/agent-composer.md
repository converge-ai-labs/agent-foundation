---
title: Agent Composer
description: A built-in agent that creates agents and new agent revisions with you, saving each one after your approval.
---

Agent Composer works in an ordinary conversation. It reads the workspace's models, skills, connections, environment templates and agents, proposes a configuration, and saves it after you approve each write.

## Start Agent Composer

1. Open **Agents → Create with AI**, or open an agent and choose **Edit with AI**.
2. Review the new conversation. **Edit with AI** fills in a request naming the agent and selected version; add the changes you want.
3. Select **Send** to start the conversation. Review each proposed write before submitting your approval.

Console prepares Agent Composer for callers with `write`. Callers with only `run` use the existing built-in agent once a builder has prepared it.

Through the API, prepare it, then [start a session](agents-and-runs.md#start-a-conversation) with the returned agent:

```sh
curl -X POST "$A13N_URL/api/v1/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

Preparing requires `write` in the workspace. The first call creates the workspace's Composer preset (`source: "builtin"`, `preset_kind: "composer"`), which `GET /api/v1/agents?source=builtin&preset_kind=composer` also finds. Later calls refresh its name and description and add a revision only when its configuration changed. Anyone with `run` can start runs with it once it exists.

## Choose its model

Agent Composer needs an enabled model the caller can use. Preparing picks, in order:

1. the model its current revision already uses, while it stays usable;
2. otherwise the first available model whose upstream name appears in the deployment's [`composer.models`](configuration.md#execution) list, in list order (a `vendor/` prefix is ignored);
3. otherwise the first usable model of the workspace, by key.

When the workspace has no usable model, preparing fails with `409 conflict` and reason `model_required`, and Console points you to model setup. [Add a model](models.md), then prepare again.

## What it can do

Agent Composer uses the same platform MCP tools as external clients. The Service supplies a short-lived credential for the current run; you do not need to create an API key. Operations remain limited by your current permissions, the run's accepted authority and its selected workspace.

| Group                 | Available work                                                                                |
| --------------------- | --------------------------------------------------------------------------------------------- |
| Agents                | Create, read, rename, label, archive and manage configuration revisions and defaults.         |
| Models                | Read, create and update configured models.                                                    |
| Connections           | Read, create, update, discover tools and test connections.                                    |
| Skills                | Validate and import uploaded or public GitHub packages, update metadata and manage revisions. |
| Environment templates | Read, create and update templates for later instances.                                        |
| Providers             | Read, create and update providers through private Console credential cards.                   |
| Execution             | Query sessions, threads, inbox entries, runs, attempts, display content, traces and findings. |

Reads run directly by default. Writes and connection tests use [approval](agents-and-runs.md#waits-approvals-and-questions); Console shows the exact call to approve or deny. Approval does not grant additional platform permissions.

To change an agent, Composer reads the selected revision and preserves unrelated fields in a complete new configuration. It retains the resource version originally read. A concurrent change causes a conflict and requires a fresh read and review. It never silently replaces the original version with the latest one.

The built-in `platform-operations` skill supplies local instructions through read-only file tools. It needs no user environment. Composer can diagnose a past failed run without knowing its session ID, and distinguishes a failed attempt from a run that later recovered. Missing trace evidence is reported as a limitation.

Composer does not edit skill package files, operate memory, configure plugins, control environment instances or start/cancel runs. It never asks for secret values in chat. Provider credentials are entered in a private Console card; connection credentials, custom secret headers and browser OAuth use existing Console pages. Unknown write outcomes require checking resource state before retrying.

## Configure a provider

Ask Composer to add or update a Model, Environment, Connector or Web Provider. It opens a card showing the proposed name, type and configuration. Enter the required credentials in that card and select **Save provider**. Only users with workspace `write` permission can save. Then select **Submit responses** to continue the conversation, answering any other pending actions first. **Cancel configuration** makes no write before a save attempt. Stopping after an unconfirmed save reports an unknown outcome to the agent.

Credentials go directly to the Provider API. They are not included in the agent's messages, tool arguments or results. The agent receives only the save status and provider ID. Updates keep saved credentials when the fields are empty and use the original resource version; a conflict needs a fresh proposal.

Saving and continuing are separate steps. After a confirmed save, retrying **Submit responses** does not save again. Saves are not automatically retried and creates are not idempotent. If the save outcome is unclear, or you reload before continuing, check **Providers** before retrying to avoid a duplicate. Browser OAuth authorization still takes place on the existing Console page.

API clients that run Composer must handle its native `configure_provider` client tool or report that it is unavailable. It uses the ordinary pending-call and batch-resume contract; no extra Run endpoint is required.

## Built-in agent rules

Agent Composer cannot be edited, revised, archived or given an avatar (`409 conflict`, reason `builtin`); only preparing changes it. Its model is selected automatically as described above. Duplicate it to create a custom agent you can change.

## Use the toolset in your own agents

Any agent can independently select platform groups and the local operating skill through its advanced configuration JSON:

```json
{
  "platform_tools": {
    "groups": ["agents", "models", "skills", "execution"],
    "read_permission": "allow",
    "write_permission": "ask"
  },
  "builtin_skills": ["platform-operations"]
}
```

Both selections default to empty. Permissions can be `allow`, `ask` or `deny`. Selecting the skill alone grants no operations. A later configuration change does not widen an active run's authority or tool selection.

The built-in Skill reads packaged documents through a read-only local Environment in the Worker. It requires no user Environment or container. Its catalog and read events use the same Harness Skills lifecycle as user Skills; when both are selected, they appear in one catalog. The Agent configuration schema ships with the documents under `/platform-skills/platform-operations/schemas`. The deployment-specific catalog under `/platform-instance` is prepared once at process startup and shared across attempts.

Keep the standard file tools enabled to read the selected Skill: `view`, `ls`, `glob` and `grep`. Composer enables only these four file tools. Builtin document mounts remain read-only; explicitly mounted user Environments follow the same file-tool permissions.

Existing agents can still use the legacy `configuration` toolset. Its `create_agent_revision` now requires the `expected_version` from the original resource read; calls missing that value must read again and submit a new request. Historical runs retain their original tools.
