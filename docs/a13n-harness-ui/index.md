# Harness UI

Harness UI is a terminal application for working with AI agents in your own projects. Ask it to explain a codebase, edit files, run checks, or delegate a focused investigation. Connect a subscription account or an API-key model, choose execution permissions, and work from the directory you already use.

You do not need to learn the Harness SDK, create a Project resource, or deploy a server to start.

## Install and start

Install the `a13n-harness-ui` command with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

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

> **Choose permissions deliberately.** Full Control is not a sandbox: commands inherit your host account's filesystem and network access. Sandbox is available on supported Linux/macOS configurations; the built-in Windows mode is Full Control. See [execution permissions](environments-and-projects.md#execution-permissions).

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

## Daily controls

| Task                                     | In chat                        |
| ---------------------------------------- | ------------------------------ |
| See available commands                   | `/help`                        |
| Switch Agent                             | `/agent`                       |
| Try another configured Model temporarily | `/model`                       |
| Change reasoning or priority service     | `/thinking`, `/fast`           |
| Change execution permissions             | `/environment`                 |
| Inspect current configuration and usage  | `/status`                      |
| Browse saved conversations               | `/resume`                      |
| Add guidance while work is running       | Type a message and press Enter |
| Cancel work                              | Ctrl+C or `/cancel`            |

See [Use the terminal](everyday-use.md) for attachments, approvals, questions, history, and recovery. Add reusable resources outside chat with `a13n-harness-ui add model` or `a13n-harness-ui add agent`.

## Go further

- **Customize:** [Agents and subagents](agents-and-subagents.md), [Models and authentication](models-and-authentication.md).
- **Connect tools:** [MCP and extensions](extensions-and-mcp.md), [native tools and Web providers](native-and-web-tools.md).
- **Work across directories:** [Environments and Projects](environments-and-projects.md).
- **Script a task or diagnose a failure:** [Automation and troubleshooting](automation-and-troubleshooting.md).

Harness UI is a local interactive Host built on [Harness](../a13n-harness/index.md). Its foreground process owns active work; it is not a detached worker service. The optional `webui` command currently provides an HTTP API and a bundled authentication/status page, not browser chat. See [browser support](automation-and-troubleshooting.md#browser-ui) before choosing it as an interface.
