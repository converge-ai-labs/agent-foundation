---
title: Harness UI
sidebarTitle: Overview
description: A terminal and browser workbench for working on real projects with agents.
---

Harness UI is the [Harness](../a13n-harness/index.md) playground for individuals and trusted small teams. Use it to work on real projects while experimenting with models, instructions, tools, Skills, and execution environments. Ask an agent to explain a codebase, edit files, run checks, or delegate a focused investigation.

The terminal offers a personal coding-agent workflow. The browser adds shared conversations and drafts, live execution, files, Git changes, terminals, and configuration editing. Both use the same application and agent foundation; neither requires SDK code or a Service deployment.

## Install and start

Install the `a13n-harness-ui` command with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

For the browser workbench, run:

```console
a13n-harness-ui webui
```

Open the login link printed by the server. The browser includes guided setup; see [Use the browser](webui.md) for collaboration, authentication, native host access, and server lifecycle.

The terminal supports macOS, Linux, and Windows. The installed application does not require Node.js or a repository checkout. If the command is missing from PATH, run `uv tool update-shell` and open a new terminal.

On first launch, setup guides you through:

1. **Connect a model:** use a supported subscription login or an API key. Existing compatible account stores can be reused.
2. **Select model settings:** choose the offered model and, where applicable, service tier. You can tune reasoning and context later.
3. **Choose execution permissions:** Full Control uses your host account; Sandbox requires working local isolation and does not silently fall back.
4. **Send your first prompt:** setup saves editable files, then opens chat. No model request is made until you send a prompt.

Try a bounded first task:

```text
Explain this repository's main entry point and tests. Do not modify any files.
```

> [!WARNING]
> Full Control runs commands with your host account's filesystem and network access; it is not a sandbox. Sandbox requires supported Linux/macOS isolation; built-in Windows execution is Full Control only. See [execution permissions](environments-and-projects.md#execution-permissions).

[Installation and upgrades](installation.md) covers source development and dependency updates. [Setup](setup.md) covers login, cancellation, and advanced choices.

## Where is my configuration?

Run this in your shell:

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
```

Or type `/config` in chat. The default root file is **`~/.a13n-harness-ui/a13n-harness-ui.yaml`**. Models and Agents live in sibling directories, not inside that file:

| Change                                           | File or action                                                                               |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Default Agent, display, built-in tools           | `a13n-harness-ui.yaml` — [root reference](configuration.md#complete-root-document)           |
| Model, endpoint, credentials, reasoning, context | `models/*.yaml` — [Model reference](models-and-authentication.md#model-file-reference)       |
| Instructions, tools, MCP selection, children     | `agents/*.yaml` — [Agent reference](agents-and-subagents.md#agent-file-reference)            |
| Global coding guidance                           | `AGENTS.md` beside the root YAML                                                             |
| Project-specific guidance                        | `AGENTS.md` in the working directory                                                         |
| Additional workspace directories                 | `projects/*.yaml` — [Project reference](environments-and-projects.md#project-file-reference) |

Start with [common configuration recipes](configuration-recipes.md) for copyable edits, or [the configuration guide](configuration.md) for all root fields and precedence. `--config PATH` selects another configuration tree; it does not merge it with the default tree.

## Terminal controls

| Task                                    | In chat                        |
| --------------------------------------- | ------------------------------ |
| See available commands                  | `/help`                        |
| Switch Agent                            | `/agent`                       |
| Select and remember a Model per Project | `/model`                       |
| Change reasoning or priority service    | `/thinking`, `/fast`           |
| Change execution permissions            | `/environment`                 |
| Inspect current configuration and usage | `/status`                      |
| Browse saved conversations              | `/resume`                      |
| Add guidance while work is running      | Type a message and press Enter |
| Cancel work                             | Ctrl+C or `/cancel`            |

See [Use the terminal](everyday-use.md) for attachments, approvals, questions, history, and recovery. Add reusable resources outside chat with `a13n-harness-ui add model` or `a13n-harness-ui add agent`.

## Go further

- **Customize:** [Agents and subagents](agents-and-subagents.md), [Models and authentication](models-and-authentication.md).
- **Connect tools:** [MCP and extensions](extensions-and-mcp.md), [native tools and Web providers](native-and-web-tools.md).
- **Work across directories:** [Environments and Projects](environments-and-projects.md).
- **Script a task or diagnose a failure:** [Automation and troubleshooting](automation-and-troubleshooting.md).
- **Build another interface:** [Embed the Python App](embedding.md) or [use the HTTP API](http-api.md).

## Sharing and execution boundaries

Share a WebUI instance only with trusted collaborators: they share credentials, configuration and accessible files, not separate participant permissions. Native Host Files and terminals are on by default; `--no-share-computer` turns them off independently of the Agent's execution mode.

Closing a browser does not stop an active Run; stopping the application does. Saved conversations can resume from the last checkpoint, but input or output since then may be lost. Use [Service](../a13n-service/index.md) for managed identities and recoverable runs.
