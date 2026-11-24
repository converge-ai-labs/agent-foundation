---
title: Common configuration recipes
sidebarTitle: Common recipes
description: Ready-to-use YAML recipes for Harness UI covering default agents, reasoning and context budgets, custom gateways, subagents, shell review, MCP, and Skills.
---

Run `a13n-harness-ui config path` to locate your configuration root (default `~/.a13n-harness-ui/a13n-harness-ui.yaml`), apply any recipe below relative to that directory, and verify with `a13n-harness-ui config validate`.

## Change the default Agent, Model, or context budget

Set the default Agent for new conversations in `a13n-harness-ui.yaml` (`defaults: {agent: agent-coder}`), and save an API key (`a13n-harness-ui auth key set key-primary`) plus a Model file under `models/`:

```yaml title="models/primary.yaml"
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  credential_ref: key-primary # or env: OPENAI_API_KEY
settings:
  thinking: high
model_characteristics:
  context_window_tokens: 128000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

Assign `model: model-primary` in your Agent YAML (`agents/coder.yaml`). Run `/model default` in TUI if the Project previously remembered another Model override.

## Connect an OpenAI-compatible gateway

Use `openai-chat:` (or `openai-responses:` when supported by the gateway) and place the endpoint URL in `model_configuration.base_url`:

```yaml title="models/compatible.yaml"
schema_version: "1"
kind: model
id: model-compatible
name: Compatible API
route: openai-chat:your-model-id
authentication:
  kind: api_key
  credential_ref: key-compatible
model_configuration:
  base_url: https://api.example.com/v1
settings: {}
```

## Configure shell review

Ask a reviewer Model (or TypeSafe Jev) to score `environment.shell_exec` calls before execution across root and child Runs (see [Shell review shortcut](configuration.md#shell-review-shortcut)):

```yaml title="a13n-harness-ui.yaml — merge into security"
security:
  shell_review:
    enable: true
    risk_threshold: extra_high
    model: model-review
    on_flagged: approval_required
    on_error: allow
```

To use TypeSafe Jev as `model-review`, save a Model with `route: typesafe:jev-latest` and `authentication: {kind: api_key, env: TYPESAFE_API_KEY}`.

## Enable built-in subagents, MCP servers, and Skills

- **Built-in subagents (`explorer`, `code-reviewer`, `executor`):** Merge `subagents: {include: [explorer, code-reviewer]}` into `a13n-harness-ui.yaml` (`[]` disables automatic inclusion; inspect with `a13n-harness-ui config subagents --format json`).
- **MCP server and Skills on an Agent:** Register `mcp/docs.yaml` ([MCP guide](mcp.md)) and merge `mcp_servers` and `skills` into your Agent YAML (placing `SKILL.md` files under `.agents/skills/<name>/`—see [Skills](skills-and-content-plugins.md)):

```yaml title="agents/coder.yaml — merge into existing Agent"
mcp_servers: [mcp-docs]
capabilities:
  - capability: dynamic_environment
    configuration: {files_enabled: true, shell_enabled: true}
  - capability: skills
    configuration: {}
```

## Tune display, tool timeouts, and multi-root Projects

```yaml title="a13n-harness-ui.yaml — merge into display and tools"
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

For multi-repository workspaces, define ordered roots in `projects/*.yaml` ([Projects](environments-and-projects.md#project-file-reference)). If an edit does not take effect, check `config path`, run `config validate` (invalid edits leave the previous accepted generation active—never delete the data directory), inspect `/status` for overrides, and start a fresh Run.
