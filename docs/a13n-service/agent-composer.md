# Agent Composer

Agent Composer is a built-in agent that creates and changes agents with you in a conversation. It reads the workspace's models, skills, connections, environment templates and agents, proposes a configuration, and saves it after you approve each write.

## Start Agent Composer

In Console, open **Agents**, expand the **Create agent** menu and choose **Create with AI**. To change an existing agent, open its detail page and choose **Edit with AI**. Console prepares Agent Composer and opens a new conversation with it.

Through the API, prepare it, then [start a session](agents-and-runs.md#start-a-conversation) with the returned agent:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/agent-composer" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

Preparing requires `write` in the workspace. The first call creates the workspace's built-in agent with key `agent-composer` (`source: "builtin"`). Later calls refresh its name and description and add a revision only when its configuration changed. Anyone with `run` can converse with it once it exists.

## Choose its model

Agent Composer needs an enabled model the caller can use. Preparing picks, in order:

1. the model its current revision already uses, while it stays usable;
2. otherwise the first available model whose upstream name appears in the deployment's [`composer.models`](configuration.md#execution) list, in list order (a `vendor/` prefix is ignored);
3. otherwise the first usable model of the workspace, by key.

When the workspace has no usable model, preparing fails with `409 conflict` and reason `model_required`, and Console points you to model setup. [Add a model](models.md), then prepare again.

## What it can do

Agent Composer uses the `configuration` toolset. Every tool acts as the principal who started the run, within that principal's permissions:

| Tool                    | Effect                                                                                                  | Default permission |
| ----------------------- | ------------------------------------------------------------------------------------------------------- | ------------------ |
| `find_resources`        | Lists agents, models, skills, connections or environment templates, 20 at a time.                       | allow              |
| `read_resource`         | Reads one of them; an agent is returned at its default revision, or the named `revision_id`.            | allow              |
| `describe_agent_config` | Returns the agent configuration schema and the toolset catalog.                                         | allow              |
| `create_agent`          | Creates an agent with a key, name, description and configuration.                                       | ask                |
| `create_agent_revision` | Adds a complete configuration as a new revision of an agent, by default making it the default revision. | ask                |

Each write stops the run and waits for your [approval](agents-and-runs.md#waits-approvals-and-questions); Console shows the proposed call to approve or deny. Refusals, such as a validation error or a missing permission, are returned to Agent Composer, which reports them.

To change an agent, Agent Composer reads the revision you name, or else its default revision, and writes a whole new revision that keeps every field you did not ask to change. It uses only resource IDs its tools returned.

Agent Composer does not rename agents or change their keys or labels, archive them, or manage connections, secrets and providers. It never asks for credential values: set up connections and secrets yourself, then ask Agent Composer to use them.

## Built-in agent rules

Agent Composer cannot be edited, revised, archived or given an avatar (`409 conflict`, reason `builtin`); only preparing changes it. Duplicate it to create a custom agent you can change. If a custom agent already uses the key `agent-composer`, preparing fails with reason `key_in_use`.

## Use the toolset in your own agents

Any agent can enable the `configuration` toolset in its revision. Its write tools default to `ask`; a revision can set another [permission](agents-and-runs.md#tool-permissions) for each tool.
