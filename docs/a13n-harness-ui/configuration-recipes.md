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

**File: `a13n-harness-ui.yaml`**

```yaml
security:
  shell_review:
    enable: true
    risk_threshold: extra_high
    model: model-review
    on_flagged: approval_required
    on_error: allow
```

Create `model-review` as a **Model resource** first, or use an existing Model ID. It is not the `code-reviewer` subagent and cannot execute tools. Subscription setup creates a separate lightweight reviewer; API-key setup reuses the connected Model. Each review makes a Model request, with the selected connection's usage and cost.

This shortcut applies to shell launches (`environment.shell_exec`) across Agents and their children, not every tool. Risk levels are `low`, `medium`, `high`, and `extra_high`; the default threshold is `extra_high`. Calls at or above the threshold ask for approval by default. Other tools are not opted into review by this shortcut.

Set `enable: false` to stop using the shortcut. **This does not remove or disable an explicitly configured Agent policy.** When enabled, the shortcut merges permissions and optional review into one `ToolPermissionsCapability` in the captured Run. Its shell permission wins even over an Agent's explicit `allow`, `deny`, or `ask`; its supplied threshold, flagged action, error action, and Model win over the corresponding Agent fields. Unrelated rules, reviewer instructions, and other settings are preserved. Omitted/null fields inherit the Agent reviewer configuration, falling back to `extra_high`, the effective Agent Model, `on_flagged: approval_required`, and `on_error: allow` when absent.

Ordinary UI authoring uses this root mapping. Advanced Agent configuration can use one `ToolPermissionsCapability` with nested `review` configuration. With the shortcut off, permission `review` is still required for the reviewer to run: a reviewer or risk rule alone does not activate review. There is no separate review Capability or compatibility alias.

`on_flagged` accepts `deny` or `approval_required`; `on_error` additionally accepts `allow`. Non-timeout reviewer errors follow the effective `on_error` policy; the default `allow` continues through all remaining checks. Reviewer timeout always denies execution. Human decisions use the separate Host `tools.interaction_timeout_seconds` (default 120). Risk/reason rendering is best effort; `/review request-id` opens details. History is advisory, not permission, and review is not filesystem or network isolation. Validate with `a13n-harness-ui config validate`; an enabled shortcut with a missing Model or invalid merged policy is an error. Accepted edits affect later Runs, never already captured execution.

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

The interaction timeout controls terminal questions, approvals, and external results, or a complete [WebUI decision batch](webui.md#questions-and-approval-timeouts); it does not limit model execution. Expiry never grants approval. Set `enable_codeact: false` to remove built-in CodeAct runners and their state tools from newly resolved Runs. Global disabled switches also take precedence over explicit Agent Capability selections.

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
