---
title: Agent Composer
description: Describe an agent, review the proposed changes, and test the saved result in Console.
---

Use Agent Composer to turn a task description into a saved agent. Composer reads the workspace's resources, proposes changes and asks you to approve writes. Start with a project-notes assistant, test its output, then return to the same conversation to refine it. Composer can also configure resources and diagnose failed runs.

## Start Agent Composer

You need an [enabled model](models.md) you can use and workspace `write` permission to prepare Composer. Once prepared, callers with `run` permission can use it.

1. Open **Agents → Create with AI**.
2. Send: “Create an agent that turns project notes into next steps. For each action, include the owner if the notes name one. Use an available model and show me the instructions before saving.”
3. Review the proposed agent configuration. Approve or deny each write, complete every pending item, then select **Submit responses** to continue.
4. Open the saved agent and select **Try agent**. Send a few notes containing one named owner and one unassigned action. Check that the response preserves both correctly.
5. To refine it, open the agent and choose **Edit with AI**. Console fills in the agent and selected version; describe the change you want. Composer reads that revision and preserves unrelated fields in the new configuration.

A write uses the resource version Composer originally read. If someone changes the resource first, ask Composer to read it again and review the new proposal.

For a new Console conversation with an existing Composer or Finding Agent, Console prepares the current definition before the first message when you have `write`. This also applies to **Try agent**. An explicitly pinned revision skips preparation. Without `write`, Console uses the existing default; a preparation permission error also falls back when a default exists. Other preparation errors stop submission and remain visible. Running still requires `run`. API clients prepare explicitly; deploying a new release alone does not refresh saved presets.

## Configure a provider

Keep credentials in the private provider card:

1. Ask Composer to add or update a Model, Environment, Connector or Web Provider. Describe the provider type and purpose without pasting credentials into the conversation.
2. Check the card's proposed name, type and configuration. Enter credentials in the card and select **Save provider**. This requires workspace `write` permission.
3. After the save is confirmed, complete the other pending items and select **Submit responses**. Composer receives the saved provider ID and can continue configuration.

The card sends credentials directly to the Provider API. Messages, tool arguments, tool results and resume payloads contain no credentials. Empty credential fields on an update preserve the saved credentials. A version conflict needs a fresh proposal using a new read.

Treat saving and continuing as separate steps. After a confirmed save, retrying **Submit responses** only retries continuation. If a save is unconfirmed, or the page reloads before continuation, inspect **Providers** before attempting another create: the first request may have succeeded. Cards do not automatically retry saves. **Cancel configuration** before any save attempt makes no write; stopping after an unconfirmed attempt reports an unknown outcome.

Use the dedicated Console pages for connection credentials, custom secret headers and browser OAuth. An API client must handle the native `configure_provider` client tool or report it unavailable through the ordinary [pending-call and batch-resume flow](agents-and-runs.md#waits-approvals-and-questions).

## What it can do

| Group                 | Available work                                                                                                  |
| --------------------- | --------------------------------------------------------------------------------------------------------------- |
| Agents                | Create, read, rename, label, archive and manage revisions and defaults.                                         |
| Models                | Read, create and update configured models.                                                                      |
| Connections           | Read, create, update, discover tools and test connections.                                                      |
| Skills                | Validate and import uploaded or public GitHub packages, update metadata and manage revisions.                   |
| Environment templates | Read, create and update templates for later instances.                                                          |
| Providers             | Read providers; create and update them through private Console credential cards.                                |
| Schedules             | Preview, create and update schedules; enable, pause, archive, dispatch and inspect trigger history.             |
| Execution             | Query sessions, threads, inbox entries, runs, attempts, display content, traces and findings; submit a finding. |

Composer uses platform MCP tools with a short-lived run credential. Reads proceed by default; writes and connection tests require [approval](agents-and-runs.md#waits-approvals-and-questions). Each operation stays within your current permissions, the run's accepted authority and its workspace. Approval does not add platform permissions.

For a [schedule](schedules.md), give Composer the time, timezone, target agent and input. Review the timing preview before approving creation. New schedules default to paused; explicitly request enablement to start future scheduled execution. The schedule retains your delegated execution authority after this conversation ends. Pause or archive it to stop future work. Future runs still apply their own tool approvals.

For diagnosis, provide a run ID or describe the failure. Composer can find failed runs without a session ID, distinguish a failed attempt from a recovered run, and identify missing trace evidence. After an uncertain write, have it inspect the saved resource before retrying.

Use the dedicated guides to [edit skill packages](skills.md), [manage memory](memory.md), [configure plugins](configuration-reference.md#plugins), [manage environment instances](environments.md) or [control runs](agents-and-runs.md). These operations are outside Composer's selected tools.

## Prepare through the API

Use a terminal with `curl` and `jq`. Set `A13N_URL` to your Service origin, without `/api/v1`, and `A13N_API_KEY` to a [workspace API key](identity.md#api-keys) whose principal has `write` and `run` permissions. The workspace must already have a usable model.

```sh
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -o composer.json
jq '{id, source, preset_kind, default_revision_id}' composer.json
```

Expect an agent with `source: "builtin"`, `preset_kind: "composer"`, an `id` and a `default_revision_id`. Use that `id` as `agent_id` in [Start a conversation](agents-and-runs.md#start-a-conversation), then send the task from the Console example above. Preparation creates or refreshes the agent; it does not start a run.

To find the preset later, request `GET /api/v1/agents?source=builtin&preset_kind=composer`. Repeating preparation updates deployment-owned name and description and offers changed protected configuration as candidate revisions. Your selected default and model stay unchanged.

## Choose its model

Initial preparation chooses an enabled model the caller can use:

1. Match the deployment's [`composer.models`](configuration.md#execution) list in preference order. Matching uses the upstream name after its final `/`.
2. If none matches, choose the first usable workspace model by key.

If no model is usable, initial preparation returns `409 conflict` with reason `model_required`. Follow Console's model-setup link or [add a model](models.md), then prepare again. Later preparation preserves your selected model even if it becomes unavailable; choose its replacement on Composer's Agent page.

## Built-in agent rules

To tune Composer, open its Agent page, change the model or allowed model settings, and save a revision. Editable settings are thinking effort, output token limit, `temperature` and `top_p`. The instruction editor saves your supplemental instructions; official instructions appear in a read-only disclosure.

Use **Versions → Set as default** to select or roll back a revision. Preparation preserves that selection and your supplement. Protected configuration updates appear as candidate versions for you to select. Other configuration, identity, avatar and archiving remain protected; duplicate Composer when you need unrestricted editing.

Official instructions come from the current Worker on every attempt, including recovery and continuation after a wait. The saved revision fixes your supplement, not the official text. The disclosure previews current deployment guidance; the model request trace shows the text used in a particular execution.

For API edits, read the selected default and the original head ETag. Preserve the complete `config`, change only the allowed model/settings and `config.instructions` supplement, then create a revision with that ETag. Revision responses expose current official text in read-only `builtin_instructions`. Duplication and export combine current official text and your supplement into an independent custom agent.

## Use the toolset in your own agents

A custom agent needs a model and a primary environment to use the `platform-operations` skill. This includes copies of Composer. Set a default environment template or choose a session environment before running; the skill is copied there using the ordinary [synchronization policy](skills.md#runtime-synchronization).

1. Open the agent's tools and select [platform tools](tools.md#platform-tools) under **Advanced / Platform Features**.
2. Enable `view`, `ls`, `glob` and `grep` so the agent can read the skill.
3. In advanced configuration JSON, select `platform-operations` and the required platform groups. Merge the following fields into the agent's complete configuration, keeping its model and environment settings:

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

This partial configuration selects agent, model, skill and execution tools while disabling agent archiving. Preserve other selected toolsets, including the file tools, when merging it. Save a revision and try a read-only task such as “List the agents available in this workspace.” Check that the agent reads the skill and returns workspace results.

Platform groups and builtin skills are unselected by default. Tools grant operations; the skill supplies instructions. Each tool retains its `permission`; `inherit` allows reads and asks before writes. Configuration edits apply to later runs; active runs keep their accepted authority and tool selection.

The built-in Composer reads the same packaged skill through read-only Worker mounts and needs no user environment or container. Its schema directory is `/platform-skills/platform-operations/schemas`, and its deployment catalog is `/platform-instance`. Composer enables only the four file tools listed above. Built-in document mounts remain read-only; explicitly mounted user environments use the same file-tool permissions.

### Computer coordinate mode

If your custom agent also uses Computer tools, choose pixel or relative coordinates in the [environment Computer configuration](environments.md#computer-coordinate-mode).
