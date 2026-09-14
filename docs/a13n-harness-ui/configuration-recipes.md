# Common configuration recipes

Use these recipes after [setup](setup.md). Each snippet says **which file to edit**. Merge fields into the existing document; do not replace unrelated settings or add a second copy of a YAML key.

Find the selected directory first:

```console
a13n-harness-ui config path
```

Paths below are relative to that directory, normally `~/.a13n-harness-ui/`. Validate after editing:

```console
a13n-harness-ui config validate
```

With a custom tree, pass the same `--config /path/to/a13n-harness-ui.yaml` before every subcommand. Validation checks configuration, not provider entitlement, a live MCP connection, or whether every Project directory is currently available.

Within supported configuration versions, unknown additive fields in configuration sections and resource metadata are preserved, with warnings that they are not applied. Check these warnings for typos. Known field types, references, authentication sources, MCP transports, and unsupported versions still fail validation. Guided updates preserve unrelated fields; replacing an entire file still replaces its contents. A newer field with meaningful behavior requires a version that understands it—preservation is not feature support.

A missing Project directory does not make the whole configuration or saved history unreadable. Restore or update the directory before running a conversation that selects it; Harness UI will not silently switch that conversation to another directory or execution mode. Already released strict readers may still reject populated new fields, so this is not a guarantee that any older package can read any newer installation.

## Change the default Agent

**File: `a13n-harness-ui.yaml`**

```yaml
defaults:
  agent: agent-coder
```

`agent-coder` must be an existing Agent's `id`. It is not the filename or display name. This initializes new conversations; existing conversations retain their selected Agent. Use `/agent agent-coder` to change an existing conversation.

To create another Agent without editing files, run `a13n-harness-ui add agent`.

## Change the model, reasoning, or context budget

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
  context_window: 128000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

- `route` selects provider and model; it does not create credentials.
- `authentication.env` names an exported variable available when Harness UI starts. For a locally saved key, replace `env` with `credential_ref: key-primary` after running `a13n-harness-ui auth key set key-primary`.
- `thinking` requests supported reasoning effort. It is independent of concise/detailed terminal display.
- `context_window` is a local working budget, not a provider limit increase. Here the reminder derives from 83,200 tokens and compaction from 115,200 tokens.
- `capabilities` declares native input support; verify the actual endpoint before enabling a modality.

Select it in **`agents/<name>.yaml`**:

```yaml
model: model-primary
```

Use `/model default` to clear the Project's remembered Model, and `/thinking default` and `/fast reset` to remove temporary request overrides when checking your permanent edits. Accepted Model edits apply to later Runs; an active Run keeps its captured settings.

For all fields and native setting behavior, see [Model reference](models-and-authentication.md#model-file-reference).

## Connect an OpenAI-compatible endpoint

**File: `models/compatible.yaml`**

```yaml
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

For all Agents in this configuration tree, edit **`AGENTS.md` beside root YAML**. For one workspace, edit **`AGENTS.md` in its working directory**. There is no ancestor-directory guidance scan. These files provide guidance; they do not grant or remove execution permissions.

## Enable selected built-in subagents

**File: `a13n-harness-ui.yaml`**

```yaml
subagents:
  include: [explorer, code-reviewer]
```

The available names are `explorer`, `code-reviewer`, and `executor`. `[]` disables automatic inclusion. There is no need to copy their Markdown files. Built-ins inherit the parent Model and are added to the root roster, not recursively to every child.

For a child with an independent model, create an Agent resource and add `- agent: agent-reviewer` to the parent Agent's `subagents`. For instructions-only roles, use [Markdown children](agents-and-subagents.md#write-a-markdown-child).

## Configure tool review

**File: `agents/<name>.yaml`, entry inside `capabilities`**

```yaml
capabilities:
  - capability: ToolReviewCapability
    configuration:
      model: model-review
      risk_threshold: extra_high
      rules:
        environment.shell_exec:
          risk_threshold: high
      on_flagged: approval_required
      on_error: approval_required
```

Preserve other Capability entries. `model-review` must be a configured **Model resource**, not a subagent ID. This reviewer examines local tool invocations; it is unrelated to delegating a code review to the `code-reviewer` child.

Preserve the shared global defaults or override individual tools. One best rule wins (exact ID, longest prefix, then `*`); omitted fields inherit global settings, not broader rules. Risk and reason come from the reviewer; runtime policy asks or denies at the threshold. This example asks at `extra_high` globally and `high` for shell launches. The UI catalog defaults to asking, unlike the Harness library's `deny`. Explicit `deny` remains supported.

The unified gate reviews all local tools by default, with compact task, Environment, previous-review, and observed-action context. History is advisory, never permission. Shell approval rendering shows risk/reason best effort; other tools keep ordinary presentation. `/review request-id` opens details. A separate tool-policy confirmation may follow reviewer approval.

Non-timeout errors follow `on_error`; the example asks, while subscription setup explicitly uses `allow`. Reviewer timeout always denies execution. Human decisions use the independent uniform Host `tools.interaction_timeout_seconds`, default 120. Legacy `ShellReviewCapability` is accepted only as a UI alias; `on_error: skip` maps to `allow`. Migrate `on_flagged: skip` to an explicit permission `allow` if review should be bypassed. Review is not filesystem or network isolation.

## Enable an MCP server

**File: `mcp/docs.yaml`**

```yaml
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

Preserve other entries. Automatic sources include Project `.agents/skills`, user `~/.agents/skills`, installed Content Plugins, and the release-owned configuration Skill through the selected Environment. See [source precedence and offline guidance](skills-and-content-plugins.md#automatic-sources-and-precedence). A Skill directory contains `SKILL.md`. Type `$` in chat to find available Skills.

Optional `configuration.roots` adds explicit absolute **Environment paths**, not arbitrary host paths. Sandbox cannot access a host directory merely because you listed it. See [Skill sources](skills-and-content-plugins.md).

## Change display and tool switches

**File: `a13n-harness-ui.yaml`**

```yaml
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

The question timeout controls a displayed terminal question, not a model request or shell approval. Set `enable_codeact: false` to remove built-in CodeAct runners and their state tools from newly resolved Runs. Global disabled switches also take precedence over explicit Agent Capability selections.

See [root fields](configuration.md#display-settings) for allowed ranges.

## Work with multiple directories

Create a [Project resource](environments-and-projects.md#project-file-reference) with ordered absolute roots. Launch the terminal from its **first** root to select it. A single-directory user does not need a Project file: the terminal uses its launch directory.

Use `/environment` to change execution mode. Project roots organize work; they do not confine Full Control's ambient host authority.

## Why did my edit not take effect?

1. Check `config path`: are you editing the tree this process selected?
2. Run `config validate`: fix invalid fields and missing references; inspect any Capability warnings.
3. Check `/status` for the selected Agent and effective Model. Remove temporary overrides if needed.
4. Wait for a **new Run**: configuration never changes an in-flight capture.
5. Start a new conversation if you changed global resource defaults. Restart for process/display startup settings.

An invalid on-disk edit can leave the last accepted configuration active. Do not delete the data directory to force an edit: it also owns saved conversations and credentials. See [configuration precedence](configuration.md#what-wins-and-when-edits-apply).
