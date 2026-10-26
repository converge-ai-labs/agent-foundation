---
title: Agent Composer
description: Create agents, configure workspace resources, and diagnose runs through a conversation.
---

Agent Composer helps you build and change agents in Console. Describe the result you want, review the proposed configuration, and approve the writes. It can also configure models, connections and providers, import skills, and investigate failed runs.

## Start Agent Composer

The workspace needs an [enabled model](models.md) you can use. Preparing Composer requires `write`; once prepared, callers with `run` can use it.

1. Open **Agents → Create with AI**, or open an agent and choose **Edit with AI**.
2. Describe the task. For example: “Create an agent that turns project notes into next steps. Use an available model and show me the instructions before saving.” **Edit with AI** fills in the agent and selected version; add your requested changes.
3. Select **Send**. Review each proposed write and approve or deny it. Complete every pending item, then select **Submit responses** to continue.
4. Open the saved agent and choose **Try agent**. Test it with representative input, then return to Composer for further changes.

To change an agent, Composer reads its selected revision and preserves unrelated fields in a complete new configuration. Writes use the resource version originally read. A concurrent change requires a fresh read and review.

Before sending the first message of a new Console session with an existing Composer or Finding Agent, Console prepares its current definition when you have `write` permission. This includes sessions opened through **Try agent**. Without `write`, or if preparation reports insufficient permission, the session uses the existing default revision; starting the run still requires `run` permission. Explicitly pinned revisions skip preparation. Other preparation errors remain visible and stop submission. Deployment alone does not refresh saved presets; API clients call the preparation endpoint explicitly.

## Configure a provider

1. Ask Composer to add or update a Model, Environment, Connector or Web Provider.
2. Review the card's proposed name, type and configuration. Enter credentials in the private card and select **Save provider**. Saving requires workspace `write` permission.
3. Answer any other pending actions, then select **Submit responses** to continue the conversation.

Credentials go directly to the Provider API. The agent receives only the save status and provider ID; credentials stay out of messages, tool arguments and results. Empty credential fields on updates keep saved credentials. Updates use the original resource version, so a conflict needs a fresh proposal.

Saving and continuing are separate steps. After a confirmed save, retrying **Submit responses** does not save again. If the save outcome is unclear, or you reload before continuing, check **Providers** before retrying: saves are not automatically retried and another create can add a duplicate. **Cancel configuration** before a save attempt makes no write; stopping after an unconfirmed save reports an unknown outcome.

Use the existing Console pages for connection credentials, custom secret headers and browser OAuth authorization. API clients running Composer handle its native `configure_provider` client tool or report it unavailable, using the ordinary pending-call and batch-resume contract.

## What it can do

| Group                 | Available work                                                                                                                     |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Agents                | Create, read, rename, label, archive and manage configuration revisions and defaults.                                              |
| Models                | Read, create and update configured models.                                                                                         |
| Connections           | Read, create, update, discover tools and test connections.                                                                         |
| Skills                | Validate and import uploaded or public GitHub packages, update metadata and manage revisions.                                      |
| Environment templates | Read, create and update templates for later instances.                                                                             |
| Providers             | Read, create and update providers through private Console credential cards.                                                        |
| Schedules             | Preview, create and update one-time or recurring schedules; enable, pause, archive, manually dispatch and inspect trigger history. |
| Execution             | Query sessions, threads, inbox entries, runs, attempts, display content, traces and findings, and submit a finding.                |

Composer uses platform MCP tools with a short-lived credential supplied for the run. Reads run directly by default; writes and connection tests use [approval](agents-and-runs.md#waits-approvals-and-questions). Operations remain within your current permissions, the run's accepted authority and its workspace. Approval grants no additional platform permissions.

For [Schedules](schedules.md), specify the time and timezone, target Agent and input. Composer previews the timing before proposing a write. Creation defaults to paused; explicitly request immediate enablement to start future scheduled execution. A Schedule retains your delegated execution authority after the Composer conversation ends. Pause or archive it to stop future work; approving its creation does not automatically approve tools used by its future Runs.

For diagnosis, give Composer a run ID or describe the failure. It can find a past failed run without its session ID and distinguish a failed attempt from a run that later recovered. Its answer identifies missing trace evidence when relevant.

Use the dedicated guides to [edit skill packages](skills.md), [manage memory](memory.md), [configure plugins](configuration-reference.md#plugins), [manage environment instances](environments.md) or [control runs](agents-and-runs.md). These operations are outside Composer's tool selection. For any uncertain write outcome, check the resource state before retrying.

## Prepare through the API

Prepare Composer, then [start a session](agents-and-runs.md#start-a-conversation) with the returned agent:

```sh
curl -X POST "$A13N_URL/api/v1/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

The first call creates the workspace's preset (`source: "builtin"`, `preset_kind: "composer"`). Find it with `GET /api/v1/agents?source=builtin&preset_kind=composer`. Later preparation refreshes its name and description and offers protected configuration updates as candidate revisions. Your selected default and model remain unchanged.

## Choose its model

Initial preparation selects an enabled model the caller can use, in this order:

1. The first available model matching the deployment's [`composer.models`](configuration.md#execution) list, in list order. Matching uses the upstream name and ignores a `vendor/` prefix.
2. The first usable workspace model, by key.

If no model is usable during initial setup, preparation returns `409 conflict` with reason `model_required`. Console links to model setup. [Add a model](models.md), then prepare again.

## Built-in agent rules

Open Composer's Agent page to change its model, thinking effort, output token limit, `temperature`, `top_p` or supplemental instructions. The instruction editor contains your supplement; official instructions appear in a read-only disclosure. Saving creates a revision containing your supplement. Official instructions come from the current Worker on every execution attempt, including recovery and continuation after waiting; they are not frozen with the revision. The disclosure shows current deployment guidance. Inspect the model request trace for the official text used in a particular run.

Use **Versions → Set as default** to switch or roll back. Later preparation preserves the selected version even when its model becomes unavailable; select a replacement explicitly. Protected configuration updates appear as candidate versions without replacing your default or supplement. Other configuration, identity, avatar and archiving remain protected. Duplicate Composer to freely change those fields.

API clients reuse validation and revision creation: read the selected default and original head ETag, keep the complete `config`, and change only the allowed model/settings and `config.instructions` supplement. Revision responses show current official text in read-only `builtin_instructions`. Composer always treats saved instructions as your supplement and receives official guidance from the Worker. Duplication and export combine current official text with your supplement into an independent custom Agent.

## Use the toolset in your own agents

Select [platform tools](tools.md#platform-tools) under **Advanced / Platform Features** in the agent's tools, and the local operating skill in advanced configuration JSON. In the configuration, this agent can manage agents, models and skills and read executions, but cannot archive agents:

```json
{
  "toolsets": {
    "agents": {
      "enabled": true,
      "tools": {
        "archive_agent": { "enabled": false }
      }
    },
    "models": { "enabled": true },
    "skills": { "enabled": true },
    "execution": { "enabled": true }
  },
  "builtin_skills": ["platform-operations"]
}
```

Platform groups and the skill are not selected by default. Each tool keeps its own `permission`; `inherit` lets reads run and asks before writes. Select tools to grant operations and the skill to supply instructions. Active runs keep their accepted authority and tool selection after configuration changes.

The `platform-operations` skill reads packaged instructions through a read-only local Environment in the Worker, with no user Environment or container required. Find the agent configuration schema under `/platform-skills/platform-operations/schemas` and the deployment catalog under `/platform-instance`.

Enable `view`, `ls`, `glob` and `grep` to read the skill. Composer enables only these four file tools. Built-in document mounts remain read-only; explicitly mounted user Environments follow the same file-tool permissions.

### Computer coordinate mode

Choose pixel or relative coordinates in the [environment Computer configuration](environments.md#computer-coordinate-mode).
