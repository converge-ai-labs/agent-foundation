---
title: Common configuration recipes
sidebarTitle: Common recipes
description: Common edits for Models, Agents, tools, MCP servers, Skills, and review policy.
---

Start with [setup](setup.md). To edit manually, find the selected directory and validate the result:

```console
a13n-harness-ui config path
a13n-harness-ui config validate
```

Merge each snippet into the named file under that directory (normally `~/.a13n-harness-ui/`). For a custom tree, put `--config /path/to/a13n-harness-ui.yaml` before the subcommand. Validation checks fields and references without connecting to providers or MCP servers. Inspect warnings: unknown additive fields are preserved but not used. Project directories must be available before a Run, not during validation.

## Change the default Agent

```yaml title="a13n-harness-ui.yaml"
defaults:
  agent: agent-coder
```

`agent-coder` must be an existing Agent's `id`. It is not the filename or display name. This initializes new conversations; existing conversations retain their selected Agent. Use `/agent agent-coder` to change an existing conversation.

To create another Agent without editing files, run `a13n-harness-ui add agent`.

## Change the Model, reasoning, or context budget

Model connection and request settings belong in **`models/<name>.yaml`**, not root YAML or the Agent's `capabilities`.

This is a complete API-key Model example. Choose a route your endpoint supports:

```yaml
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
settings:
  thinking: high
  openai_service_tier: default
model_characteristics:
  capabilities: [image_understanding]
  context_window_tokens: 128000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

- `route` selects provider and model; it does not create credentials.
- `authentication.env` names an exported variable available when Harness UI starts. For a locally saved key, replace `env` with `credential_ref: key-primary` after running `a13n-harness-ui auth key set key-primary`.
- `thinking` requests supported reasoning effort. It is independent of concise/detailed TUI display.
- `context_window_tokens` is a local working budget, not a provider limit increase. Here the reminder derives from 83,200 tokens and compaction from 115,200 tokens.
- `capabilities` declares native input support; verify the actual endpoint before enabling a modality.

Select it in **`agents/<name>.yaml`**:

```yaml
model: model-primary
```

Use `/model default` to clear the Project's remembered Model, and `/thinking default`, `/fast reset`, and `/pro reset` to remove temporary request overrides when checking your permanent edits. Accepted Model edits apply to later Runs; an active Run keeps its captured settings.

For all fields and native setting behavior, see [Model reference](models-and-authentication.md#model-file-reference).

## Connect an OpenAI-compatible endpoint

```yaml title="models/compatible.yaml"
schema_version: "1"
kind: model
id: model-compatible
name: Compatible API
route: openai-chat:your-model-id
authentication:
  kind: api_key
  env: COMPATIBLE_API_KEY
model_configuration:
  base_url: https://api.example.com/v1
settings: {}
```

Replace the model ID and URL. Use the protocol the endpoint implements; OpenAI-compatible Chat Completions is not the same as Responses. Local HTTP endpoints are supported. URLs cannot contain credentials, query parameters, or fragments.

`model_configuration.base_url` configures the connection; `settings` configures requests. Subscription routes and the native `xai:` SDK route do not accept this HTTP override. To preserve provider-specific reasoning behavior, prefer a supported native route such as `deepseek:`, `zai:`, or `moonshotai:` over a generic compatible connection.

## Add coding instructions

For one Agent, edit **`agents/<name>.yaml`**:

```yaml
instructions: |
  Read the relevant code before editing.
  Keep changes focused and validate the affected behavior.
  Report changed files, checks, and remaining limitations.
```

For all Agents in this configuration tree, edit **`AGENTS.md` beside root YAML**. For one Project or directory, edit **`AGENTS.md` in its working directory**. There is no ancestor-directory guidance scan. These files provide guidance; they do not grant or remove execution permissions.

## Enable selected built-in subagents

```yaml title="a13n-harness-ui.yaml"
subagents:
  include: [explorer, code-reviewer]
```

The available names are `explorer`, `code-reviewer`, and `executor`. `[]` disables automatic inclusion. There is no need to copy their Markdown files. Built-ins inherit the parent Model and are added to the root roster, not recursively to every child.

For a child with an independent Model, create an Agent resource and add `- agent: agent-reviewer` to the parent Agent's `subagents`. For instructions-only roles, use [Markdown subagents](agents-and-subagents.md#write-a-markdown-subagent).

## Configure shell review

```yaml title="a13n-harness-ui.yaml"
security:
  shell_review:
    enable: true
    risk_threshold: extra_high
    model: model-review
    on_flagged: approval_required
    on_error: allow
```

1. Create `model-review` as a Model resource, or choose an existing Model ID. The reviewer is not the `code-reviewer` subagent and cannot execute tools.
2. Add the mapping above to root YAML and run `a13n-harness-ui config validate`.
3. Start a new Run. The shortcut reviews `environment.shell_exec` for the Agent and its children; it does not enable review for other tools.

Codex and Grok setup create a separate reviewer Model. ChatGPT, GitHub Copilot, and API-key setup reuse the connected Model. Each review makes a Model request with that connection's usage and cost. For optional Codex Guardian linking, see [the root reference](configuration.md#shell-review-shortcut).

| Setting                | Behavior                                                                              |
| ---------------------- | ------------------------------------------------------------------------------------- |
| `risk_threshold`       | `low`, `medium`, `high`, or `extra_high`; calls at or above the threshold are flagged |
| `on_flagged`           | `deny` or `approval_required`                                                         |
| `on_error`             | `deny`, `approval_required`, or `allow` for non-timeout errors                        |
| Reviewer timeout       | Always denies execution                                                               |
| Human approval timeout | Separate `tools.interaction_timeout_seconds`, default 120 seconds                     |

The shortcut merges into the Agent's `ToolPermissionsCapability`. Its shell `review` rule replaces even explicit `allow`, `deny`, or `ask`; supplied review fields replace corresponding Agent fields. Unrelated rules and reviewer instructions remain. Omitted/null fields inherit the Agent policy, then fall back to `extra_high`, the effective Agent Model, `approval_required` for flagged calls, and `allow` for non-timeout errors. `allow` still runs the remaining permission checks.

Set `enable: false` to stop injecting the shortcut; explicit Agent policies remain. Advanced policies use one `ToolPermissionsCapability` with nested `review`, which runs only for tools assigned permission `review`. A reviewer or risk rule alone does not activate review.

Use `/review request-id` to inspect available risk and reason details. Review history is evidence, not a permission grant. Review does not isolate files or networks. Missing Models and invalid merged policies fail validation. Accepted edits apply to later Runs.

### Use TypeSafe Jev for review

Jev is a normal API-key Model, not a subagent or a separate review service. Create `models/jev-review.yaml`:

```yaml title="models/jev-review.yaml"
schema_version: "1"
kind: model
id: model-jev-review
name: Jev tool review
route: typesafe:jev-latest
authentication:
  kind: api_key
  env: TYPESAFE_API_KEY
```

Set `security.shell_review.model: model-jev-review` in the root document and export `TYPESAFE_API_KEY` before starting Harness UI. Keep your conversational Agent on a text-capable Model. You can replace `jev-latest` with a tested versioned Jev ID for reproducible evaluations.

To use a TypeSafe-compatible gateway instead of the default `https://api.typesafe.ai`, add this to the Model document (the endpoint must speak the native TypeSafe protocol, not OpenAI Chat Completions):

```yaml
model_configuration:
  base_url: https://jev-gateway.example
```

Jev grades severity as 0 = `low`, 1 = `medium`, 2 = `high`, 3 = `extra_high`; confidence does not change the decision. Jev returns no text explanation, and Harness UI does not request one from a second Model. Normal thresholds, permissions, timeouts, and error policy apply. Evaluate representative and adversarial commands before replacing an existing reviewer.

## Enable an MCP server

```yaml title="mcp/docs.yaml"
schema_version: "1"
kind: mcp_server
id: mcp-docs
name: Documentation server
transport:
  url: https://mcp.example.com/mcp
  headers:
    Authorization: "Bearer ${DOCS_MCP_TOKEN}"
```

Replace the endpoint and export its token in the process launching Harness UI. Then select the ID in **`agents/<name>.yaml`**:

```yaml
mcp_servers: [mcp-docs]
```

Creating the server file only registers it; selection enables it. `mcp_servers: null` inherits defaults when the conversation's selections are initialized; `[]` selects none. Existing conversations keep their MCP selections, so use a new conversation when testing changed defaults.

See [MCP configuration](mcp.md) for command transport, client-style JSON, credential references, and capture behavior.

## Enable Skills

**File: `agents/<name>.yaml`, entry inside `capabilities`**

```yaml
capabilities:
  - capability: skills
    configuration: {}
```

Preserve other entries. Automatic sources include Project `.agents/skills`, user `~/.agents/skills`, installed Content Plugins, and the release-owned configuration Skill through the selected Environment. See [source precedence and offline guidance](skills-and-content-plugins.md#automatic-sources-and-precedence). A Skill directory contains `SKILL.md`. Type `$` in the TUI to find available Skills.

Optional `configuration.roots` adds explicit absolute **Environment paths**, not arbitrary host paths. Sandbox cannot access a host directory merely because you listed it. See [Skill sources](skills-and-content-plugins.md).

## Change display and tool switches

```yaml title="a13n-harness-ui.yaml"
display:
  theme: dark
  mode: detailed
  show_status: true
  max_tool_result_lines: 15
  max_tool_argument_chars: 8192
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 300
  enable_codeact: true
```

Display defaults take effect at startup; `/theme` and `/mode` change live presentation. They do not change model reasoning, permissions, or saved model context.

The interaction timeout controls TUI questions, approvals, and external results, or a complete [WebUI decision batch](webui.md#questions-and-approval-timeouts); it does not limit model execution. Expiry never grants approval. Set `enable_codeact: false` to remove built-in CodeAct runners and their state tools from newly resolved Runs. Global disabled switches also take precedence over explicit Agent Capability selections.

See [root fields](configuration.md#display-settings) for allowed ranges.

## Work with multiple directories

Create a [Project resource](environments-and-projects.md#project-file-reference) with ordered absolute roots. Launch the TUI from its **first** root to select it. A single-directory user does not need a Project file: the TUI uses its launch directory.

Use `/environment` to change execution mode. Project roots organize work; they do not confine Full Control's ambient host authority.

## Why did my edit not take effect?

1. Check `config path`: are you editing the tree this process selected?
2. Run `config validate`: fix invalid fields and missing references; inspect any Capability warnings.
3. Check `/status` for the selected Agent and effective Model.
4. If temporary overrides are set, remove them.
5. Wait for a **new Run**: configuration never changes an in-flight capture.
6. If you changed global resource defaults, start a new conversation.
7. If you changed process or display settings, restart Harness UI.

An invalid on-disk edit can leave the last accepted configuration active. Do not delete the data directory to force an edit: it also owns saved conversations and credentials. See [configuration precedence](configuration.md#what-wins-and-when-edits-apply).
