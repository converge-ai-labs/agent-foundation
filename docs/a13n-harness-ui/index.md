---
title: Harness UI
sidebarTitle: Overview
description: Start an Agent in your terminal or browser, then configure it for your work.
---

Harness UI lets you work with an Agent on your own computer or a connected Environment. Use it to understand a repository, make changes, run checks, or investigate a problem. You supply a model subscription or API key; Harness UI supplies the conversation, tools, and configuration.

## Install and start

For your first session, follow these two guides in order:

1. [Install Harness UI](installation.md). The package includes the terminal interface and WebUI.
2. [Set up your first Agent](setup.md). Connect a Model, choose execution permissions, and send a test message.

You do not need to write YAML or create a Project before starting. Once the first message works, use [the terminal guide](everyday-use.md) or [the WebUI guide](webui.md) to work with files and follow-up tasks.

## Choose how you work

**Terminal:** launch `a13n-harness-ui` from the repository you want to work on. The terminal interface stays in that working directory. [Learn the conversation controls](everyday-use.md).

**Browser:** launch `a13n-harness-ui webui` and open the printed login link. WebUI provides conversations, files, Git changes, and terminals. [Start a browser session](webui.md).

**Voice:** enable [Live Mode](live-mode.md) in WebUI to talk through work. Live requires a supported Codex subscription Model and browser microphone access.

## Configure the workbench

Three resources explain most configuration tasks:

- A **Model** defines the provider connection and request settings.
- An **Agent** selects a Model and adds instructions and tools.
- A **Project** groups working directories and creation defaults.

Start with [common configuration recipes](configuration-recipes.md) when you want to change one of these. Use the [configuration reference](configuration.md) to look up fields and precedence.

## Shape your workflow

Add one capability at a time, after your first conversation works:

- **Change how the Agent thinks:** [connect another Model](models-and-authentication.md), then [configure an Agent](agents-and-subagents.md).
- **Choose where it works:** [select an Environment and organize Projects](environments-and-projects.md).
- **Teach a repeatable task:** [add a Skill](skills-and-content-plugins.md).
- **Connect another tool service:** [add an MCP server](mcp.md); use [MCP Apps](mcp-apps.md) for interactive results.
- **Use provider tools or media:** [configure native and Web tools](native-and-web-tools.md).

The [command reference](command-reference.md) lists terminal commands once you know the workflow you need.

## Share and continue work

Keep Harness UI running while the Agent works. Closing a browser tab leaves active Runs running; stopping the application ends that live execution. After a restart, continue from the last saved checkpoint as described in [recovery](automation-and-troubleshooting.md).

Share a WebUI instance only with trusted collaborators. They use the same credentials, configuration, and files. Native Host file access and terminals are enabled by default; `--no-share-computer` disables that sharing separately from Agent execution permissions.

For managed users, roles, and durable execution across workers, use [Service](../a13n-service/index.md).

## Build on Harness UI

To build another interface over the same configuration and conversation store, use the [Python App](embedding.md) or [HTTP API](http-api.md). To embed Agent execution in your own application with your own storage and access policy, start with [Harness](../a13n-harness/getting-started.md).
