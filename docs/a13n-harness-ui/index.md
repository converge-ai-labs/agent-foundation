---
title: Harness UI
sidebarTitle: Overview
description: High-productivity terminal and browser workbench for interacting with codebases, managing multi-agent workflows, and customizing agent capabilities.
---

**Harness UI** is an interactive, developer-first workbench designed for individuals and DIY builders. Whether you want a responsive terminal interface (TUI) to inspect git repositories and run commands alongside an agent, or a modern browser interface (WebUI) with file trees, git diff viewers, and real-time voice, Harness UI provides an out-of-the-box playground backed by the Harness execution engine.

## Install and start

Get up and running in your repository with two straightforward steps:

1. [Install Harness UI](installation.md): Install the unified package containing both the CLI terminal application and the web service.
2. [Set up your first Agent](setup.md): Connect an API key or subscription model, grant execution permissions, and start your first conversation.

No initial configuration files or database setups are required. As soon as the first message succeeds, you can work immediately via terminal or browser.

## Choose how you work

Harness UI adapts to your preferred working environment:

- **Terminal (TUI):** Run `a13n-harness-ui` directly in any project directory. The TUI keeps you focused inside your shell, supporting hotkeys, interactive decision cards, and live prompt steering. [Master the terminal interface](everyday-use.md).
- **Browser (WebUI):** Run `a13n-harness-ui webui` and open the generated link. Enjoy rich visual Markdown rendering, a collapsible file explorer, syntax-highlighted diffs, and integrated terminal panels. [Explore the WebUI guide](webui.md).
- **Voice interaction (Live Mode):** Speak directly with an agent in WebUI using low-latency WebRTC audio streaming, allowing hands-free guidance while inspecting code changes. [Learn Live Mode](live-mode.md).
- **Editor & ACP clients:** Connect Zed, JetBrains AI Assistant, or custom scripts over stdio using `a13n-harness-ui acp`. [Connect an ACP client](agent-client-protocol.md).

## Configure the workbench

Harness UI is configured through three core declarative primitives:

| Primitive   | Role in your workbench                                                    | Where to configure                       |
| :---------- | :------------------------------------------------------------------------ | :--------------------------------------- |
| **Model**   | Provider credentials, upstream model IDs, and generation settings         | `models/` directory or interactive setup |
| **Agent**   | System prompt, default model, tool permissions, and custom subagents      | `agents/` directory                      |
| **Project** | Working directory roots, local settings, and default environment bindings | `.a13n-project.yaml` or workspace roots  |

Review [configuration recipes](configuration-recipes.md) for ready-to-use patterns, or consult the [configuration reference](configuration.md) for complete YAML syntax.

## Shape your workflow

Customize and extend your workbench as your project needs grow:

- **Connect alternative models:** Switch easily between OpenAI, Anthropic, Gemini, or local Ollama servers via [models and authentication](models-and-authentication.md).
- **Organize subagents:** Define specialized child roles (e.g., code reviewer, researcher) using [agents and subagents](agents-and-subagents.md).
- **Teach repeatable workflows:** Create structured Markdown procedural guides with [Skills and Content Plugins](skills-and-content-plugins.md).
- **Preserve working notes:** Store personal guidelines and project memories across conversations via [file memory](configuration.md#file-memory).
- **Connect external tools:** Attach external tool ecosystems through [MCP](mcp.md) and render dynamic browser widgets via [MCP Apps](mcp-apps.md).
- **Add native and web tools:** Enable desktop computer use, browser scraping, and search tools using [native and web tools](native-and-web-tools.md).

Check the [command reference](command-reference.md) for a comprehensive list of CLI arguments and slash commands.

## Share and continue work

Your conversational context, execution history, and drafts are automatically preserved in the local database.

- **Conversation continuity:** Closing a browser tab or exiting the TUI does not lose work. Simply run `/resume` in the TUI or select the thread in the WebUI sidebar to continue.
- **Graceful recovery:** In the event of an unexpected process exit, Harness UI preserves intermediate drafts and saves execution checkpoints for one-click resumption.
- **Collaborative access:** Harness UI operates as a trusted, high-privilege workbench instance. When sharing an instance across a local team, you share the instance's credentials, project files, and terminals.

For centralized multi-tenant deployments requiring fine-grained user roles and remote worker clusters, deploy [Service](../a13n-service/index.md).

## Build on Harness UI

Harness UI exposes programmatic interfaces for builders:

- **Embed as a Python App:** Import and mount the complete workbench inside custom desktop wrappers or local services using the [Python App interface](embedding.md).
- **Automate via HTTP:** Integrate third-party scripts, editor extensions, or webhooks via the [HTTP API](http-api.md).
- **Embed raw execution:** If you want pure agent execution without UI layers, integrate the underlying [Harness SDK](../a13n-harness/getting-started.md).
