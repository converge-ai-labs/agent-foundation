---
title: Agents and subagents
description: Define custom YAML Agents and lightweight Markdown subagent roles with tailored instructions, inherited or dedicated models, and filtered tool permissions.
---

An **Agent** (`agents/*.yaml`) pairs a Model with instructions, Capabilities, and optional subagents. Start with one root Agent, then add built-in, dynamic, or custom subagents when you want to delegate focused tasks.

## Create an Agent from files

Run `a13n-harness-ui config path` to locate your config directory (normally `~/.a13n-harness-ui/`).

1. **Create a Model** (or reuse an existing Model ID from [setup](setup.md)):

```yaml title="models/primary.yaml"
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
```

2. **Create the root Agent** in `agents/coder.yaml`:

```yaml title="agents/coder.yaml"
schema_version: "1"
kind: agent
id: agent-coder
name: Coding agent
model: model-primary
instructions: |
  Make focused changes. Read the relevant code first, validate the changed
  behavior, and report the result.
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: true
subagents: []
```

3. **Validate and run:**

```console
a13n-harness-ui config validate
cd /absolute/path/to/your-repository
a13n-harness-ui --agent agent-coder
```

To make it the default Agent, set `defaults: {agent: agent-coder}` in `a13n-harness-ui.yaml`.

## Built-in subagents

Harness UI ships three built-in helpers configured in your **root YAML** (`a13n-harness-ui.yaml`):

| Name            | Role                                                  |
| --------------- | ----------------------------------------------------- |
| `explorer`      | Locate implementations and return file references     |
| `code-reviewer` | Review changes and report material defects            |
| `executor`      | Complete a bounded task with a clear expected outcome |

```yaml title="a13n-harness-ui.yaml"
subagents:
  include: [explorer, code-reviewer]
```

Built-in helpers inherit the parent's Model, Capabilities, and visible tools. Inspect them with `a13n-harness-ui config subagents`. To attach a single built-in role to one Agent only, add `- markdown: subagent-builtin-explorer` to that Agent's `subagents` list.

## Dynamic subagents and result delivery

Enable `spawn_subagent` so root conversations can create task-specific leaf helpers on demand using the current conversation’s Model:

```yaml title="a13n-harness-ui.yaml"
subagents:
  dynamic:
    enabled: true
```

- Declared and dynamic subagents can use the parent’s effective Model even when `agent_creation.allowed_models` is empty. Add Model IDs to `allowed_models` to make other Models available. To use a different Model when `spawn_subagent` omits `model`, set `agent_creation.default_model` to one of those IDs.
- Dynamic helpers inherit the parent's tools and Environment, start with a clean history, and report final results back automatically.
- Agents manage running children with `subagent_info`, `wait_subagent`, `steer_subagent`, and `resume_subagent`.

## Write a Markdown subagent

Use a Markdown file under `subagents/` for a helper that shares its parent's Model and Capabilities but needs focused instructions or a restricted tool list:

```markdown title="subagents/investigator.md"
---
name: investigator
description: Trace a bounded repository question and report evidence.
instruction: Use for focused read-only discovery before implementation.
tools: [glob, grep, ls, view]
---

Find the relevant implementation and tests. Return exact file paths and explain
how the pieces connect. Do not modify files.
```

Reference it in `agents/coder.yaml`:

```yaml
subagents:
  - markdown: subagent-investigator
```

## Reference an existing Agent as a subagent

When a subagent needs its own Model, Capabilities, or MCP servers, define it as a normal `agents/*.yaml` file and reference it by ID (its `name` and `instructions` come from that Agent file):

```yaml title="agents/coder.yaml"
subagents:
  - agent: agent-specialist
```

## Instructions and guidance

- **`instructions`:** Defined on the Agent YAML.
- **Global `AGENTS.md`:** Placed in your Harness UI config directory; injected into root and child conversations.
- **Project `AGENTS.md`:** Discovered automatically from the active workspace root.

## Tool search groups

When an Agent has many tools (such as large MCP servers), configure `tool_search` groups on the Agent to defer tool schemas until the Agent searches for them, reducing prompt token usage without changing tool permissions.

## Agent file reference

| Field                            | Default  | Purpose                                                             |
| -------------------------------- | -------- | ------------------------------------------------------------------- |
| `id`, `name`                     | Required | Unique `agent-` ID and display name                                 |
| `model`                          | Required | Saved Model ID                                                      |
| `instructions`                   | `""`     | Role prompt for the Agent                                           |
| `capabilities`                   | `[]`     | Enabled Capabilities (`dynamic_environment`, `web`, `skills`, etc.) |
| `tools`                          | `null`   | Optional exact-name allowlist filtering visible tools               |
| `subagents`                      | `[]`     | List of `markdown:` or `agent:` child entries                       |
| `mcp_servers`, `harness_plugins` | `null`   | Selected MCP server IDs (`null` inherits defaults) and plugin IDs   |
| `tool_search`                    | `null`   | Optional grouped tool discovery plan                                |
