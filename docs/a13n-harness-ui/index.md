---
title: Harness UI
sidebarTitle: Overview
description: Work with Agents in your terminal and browser, and coordinate tasks by voice.
---

Harness UI is a workbench for individuals and trusted small teams. Ask an Agent to explore a repository, edit files, run checks, or investigate a problem. Choose its Model, tools, Skills, and execution Environment to suit the work.

## Install and start

Install with [uv](https://docs.astral.sh/uv/getting-started/installation/), then launch from your project:

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

Setup connects a Model using a supported subscription or API key and lets you choose execution permissions. Full Control uses your Host account; Sandbox uses supported Linux/macOS isolation. Windows built-in execution supports Full Control. See [Setup](setup.md) for the steps and [Installation](installation.md) for upgrades.

Try a first prompt:

```text
Explain this repository's main entry point and tests. Do not modify any files.
```

## Choose how you work

| Interface | Use it for                                                              | Start here                       |
| --------- | ----------------------------------------------------------------------- | -------------------------------- |
| TUI       | Prompt, steer, and inspect work from your terminal                      | [Use the TUI](everyday-use.md)   |
| WebUI     | Work with conversations, files, Git changes, and terminals in a browser | [Use WebUI](webui.md)            |
| Live      | Talk through tasks and decisions while moving around the workspace      | [Enable Live Mode](live-mode.md) |

Start the browser server with:

```console
a13n-harness-ui webui
```

Open its printed login link. The browser includes guided setup. Live adds continuous voice to WebUI using a supported Codex subscription Model and browser microphone access.

## Shape your workflow

Start with the setup defaults, then add what the task needs:

| Goal                                                 | Guide                                                       |
| ---------------------------------------------------- | ----------------------------------------------------------- |
| Switch models, accounts, or reasoning settings       | [Models and authentication](models-and-authentication.md)   |
| Define an Agent and delegate tasks                   | [Agents and subagents](agents-and-subagents.md)             |
| Organize repositories and choose where tools execute | [Environments and Projects](environments-and-projects.md)   |
| Add reusable instructions                            | [Skills and Content Plugins](skills-and-content-plugins.md) |
| Connect external tools and interactive results       | [MCP](mcp.md) and [MCP Apps](mcp-apps.md)                   |
| Search the web and read files or media               | [Native and Web tools](native-and-web-tools.md)             |

In the terminal, `/agent` and `/model` change the current selection, `/status` shows settings and usage, and `/resume` opens saved conversations. See the [command reference](command-reference.md) for the full command set.

## Configure the workbench

The default configuration lives in `~/.a13n-harness-ui/`. The root YAML selects defaults; sibling files define Models, Agents, Projects, and extensions. Edit them directly or use WebUI's configuration editor.

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
```

Use [configuration recipes](configuration-recipes.md) for common edits and the [configuration reference](configuration.md) for fields and precedence. Add project instructions in the repository's `AGENTS.md`.

## Share and continue work

A shared WebUI instance gives trusted collaborators access to the same credentials, configuration, and files. Native Host file access and terminals are enabled by default; `--no-share-computer` disables them independently of Agent execution permissions.

Keep the application running while Agents work. Closing the browser leaves Runs active. After an application restart, continue from the last saved checkpoint. [Automation and troubleshooting](automation-and-troubleshooting.md) covers saved state and recovery.

For a team platform with managed users, roles, and durable execution, use [Service](../a13n-service/index.md).

## Build on Harness UI

Embed the [Python App](embedding.md) or use its [HTTP API](http-api.md) to build another interface. Both use the same configuration and conversation store as the TUI and WebUI. [Harness](../a13n-harness/index.md) provides the underlying Agent execution library.
