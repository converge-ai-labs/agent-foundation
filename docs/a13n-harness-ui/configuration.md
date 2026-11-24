---
title: Configuration reference
description: Reference for Harness UI's root a13n-harness-ui.yaml file, resource directories, precedence rules, built-in tools, memory, and WebUI server settings.
---

Harness UI reads one root file (`a13n-harness-ui.yaml`) and sibling resource directories (`models/`, `agents/`, `projects/`, `devices/`, `mcp_servers/`, `subagents/`, `web_providers/`).

## Locate and validate your files

```console
a13n-harness-ui config path
a13n-harness-ui config validate
```

Default root path: `~/.a13n-harness-ui/a13n-harness-ui.yaml`. Pass `--config /path/to/a13n-harness-ui.yaml` before any subcommand to use an alternate tree.

| Resource directory / file    | Owns                                                                                                         |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `models/*.yaml`              | [Provider connections, credentials, and request settings](models-and-authentication.md#model-file-reference) |
| `agents/*.yaml`              | [Instructions, Model selection, Capabilities, and children](agents-and-subagents.md#agent-file-reference)    |
| `web_providers/*.yaml`       | [Reusable Host search/scrape accounts](native-and-web-tools.md#web-provider-accounts)                        |
| `subscription_groups/*.yaml` | [Ordered subscription account groups](models-and-authentication.md#account-store-locations)                  |
| `projects/*.yaml`            | [Working roots, Device bindings, and defaults](environments-and-projects.md#project-file-reference)          |
| `devices/*.yaml`             | [Remote Envd Device connections](environments-and-projects.md#add-device-bindings)                           |
| `mcp/*.yaml`                 | [MCP server definitions](mcp.md#mcp-field-reference)                                                         |
| `subagents/*.md`             | [Lightweight Markdown subagents](agents-and-subagents.md#write-a-markdown-subagent)                          |
| `AGENTS.md`                  | [Global agent instructions](agents-and-subagents.md#instructions-and-guidance)                               |

## What wins and when edits apply

New conversations resolve defaults in order: **explicit CLI/UI choice → Project defaults → Agent defaults → root `defaults`**. Active Runs keep their captured configuration; edits apply to subsequent Runs.

## Minimal root file

```yaml title="a13n-harness-ui.yaml"
schema_version: "1"
defaults:
  agent: agent-coder
  environment_profile: environment-native
```

## Built-in tools and subagents

```yaml title="a13n-harness-ui.yaml — tools, input, and subagents"
tools:
  enable_ask_user_question: true
  enable_codeact: true
  interaction_timeout_seconds: 120
input:
  steering_mode: immediate # or queued
subagents:
  include: [explorer, code-reviewer, executor]
  max_concurrent: 8
  dynamic:
    enabled: true
    allowed_models: [model-primary]
    default_model: model-primary
```

- `tools.enable_ask_user_question` / `enable_codeact`: Global switches that gate both automatic and explicit Agent selections.
- `input.steering_mode`: `immediate` (default) interrupts model generation or releases supported tool waits; `queued` delivers at the next safe boundary.

## Shell review shortcut

Enable automatic LLM review of `environment.shell_exec` calls across root and child Runs:

```yaml title="a13n-harness-ui.yaml — security.shell_review"
security:
  shell_review:
    enable: true
    model: model-reviewer
    risk_threshold: extra_high
    on_flagged: approval_required
    on_error: allow
```

See [Configure shell review](configuration-recipes.md#configure-shell-review) for approval thresholds and Codex Guardian linking.

## Context recovery

Automatic context recovery is enabled by default to recover from provider context-window overflow independently of proactive compaction.

## Display settings

Configure TUI/WebUI display preferences, notification sounds, and Inbox posting (`inbox.enabled: true`) in `a13n-harness-ui.yaml` or **Settings → General**.

## File memory

Enable persistent Global and Project Markdown memory under `memory/`:

```yaml title="a13n-harness-ui.yaml — memory"
memory:
  enabled: true
  auto_organize:
    enabled: true
    model: model-primary
```

View memory files and organization history in WebUI's **Memory** panel.

## Media understanding and voice input

- `media_understanding` (`image`, `audio`, `video`): Fallback saved Model IDs when the active Model cannot read that media natively. See [Media understanding defaults](models-and-authentication.md#media-understanding-defaults).
- `dictation`: Configure Codex subscription or audio-input Model voice dictation in **Settings → Chats → Dictation** (see [Voice input](webui.md#voice-input)).

## Local envd runtime

Configure the managed or custom `a13n-envd` executable, local Computer Use (`computer_use: true`), and resource monitoring interval (`monitoring_interval_seconds: 60`, range 10–60) under `envd:` in `a13n-harness-ui.yaml`. See [Manage local envd](environments-and-projects.md#manage-local-envd).

## Host browser

Override the Chromium executable path or sandbox flags under `browser:` in `a13n-harness-ui.yaml`. See [Host browser](host-browser.md).

## WebUI allowed origins

Restrict accepted `Host` / `Origin` addresses when running WebUI behind a reverse proxy:

```yaml title="a13n-harness-ui.yaml — webui.allowed_origins"
webui:
  allowed_origins:
    - https://workbench.example.com
```

Default `[]` allows only the listener address; `["*"]` permits any origin. See [Reverse proxies](running-webui.md#reverse-proxies-and-public-addresses).

## WebUI Bot

Configure the optional always-on Bot under `webui.bot:` in `a13n-harness-ui.yaml`. See [Bot](bot.md).

## MCP host ownership

- `mcp.host_owned_servers`: List of MCP server IDs whose clients stay alive across Runs.
- `mcp.protocol_overrides`: Map of server IDs to `auto`, `legacy`, or `2026-07-28`.

## Object store

Harness UI automatically retains a local Thread-scoped Object Store (`ObjectStoreSettings` in `HarnessUiSettings` when embedding) so media and large outputs can be reused across tool calls via `object_ref_tools` without repeating base64 payloads in model history.
