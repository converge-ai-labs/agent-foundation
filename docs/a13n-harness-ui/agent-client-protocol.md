---
title: Connect an ACP client to Harness UI
description: Use Harness UI as an ACP v1 agent in Zed or JetBrains, or launch it over stdio from another program without interactive approvals.
---

`a13n-harness-ui acp` serves [Agent Client Protocol (ACP)](https://agentclientprotocol.com/) v1 over `stdin` and `stdout` on macOS and Linux, backed by your local Harness UI configuration and durable SQLite Threads. Diagnostics go to `stderr`.

## Connect your editor

Complete [Harness UI setup](setup.md) to configure a Model and Agent, then run `command -v a13n-harness-ui` in a terminal to find the executable's absolute path (GUI editors often do not inherit your shell `PATH`).

In Zed, add a custom agent to your settings:

```json
{
  "agent_servers": {
    "Harness UI": {
      "type": "custom",
      "command": "/absolute/path/to/a13n-harness-ui",
      "args": ["acp"]
    }
  }
}
```

In JetBrains AI Assistant, add a custom ACP agent (for clients configured through `~/.jetbrains/acp.json`):

```json
{
  "agent_servers": {
    "Harness UI": {
      "command": "/absolute/path/to/a13n-harness-ui",
      "args": ["acp"]
    }
  }
}
```

Select **Harness UI** in the client and send a short prompt. In interactive mode, Harness UI forwards tool approval requests to the editor with one-shot **Allow once** and **Reject once** choices (saved permission rules remain unchanged).

To select another configuration tree, pass global flags before `acp`:

```json
"args": ["--config", "/absolute/path/to/a13n-harness-ui.yaml", "acp"]
```

`acp` supports `--config`, `--data-root`, and `--no-update-check`. `--resume`, `--agent`, `--environment-mode`, and `--environment-profile` are rejected on startup because ACP selects Threads over the protocol and uses configured Agent and Environment defaults.

## Run from another program

Spawn `a13n-harness-ui acp --unattended` with pipes for `stdin` and `stdout`, attach an ACP v1 client to those pipes, and read diagnostics from `stderr`:

1. Call `initialize`.
2. Call `session/new` with an absolute workspace `cwd`, or `session/resume` with an existing session ID and its first saved local root (resume failures return an error without creating a new session).
3. Send `session/prompt`, follow live `session/update` notifications while awaiting the response, and use `session/cancel` to stop an active turn. Wait for the terminal prompt result before treating streamed text as the final answer.

Unattended mode hides tools that require human input. Shell remains available where permitted, and any Shell command whose review requires human approval fails that tool call. A Thread with a saved pending decision blocks admission until you resolve it in an interactive surface (TUI or WebUI). For a single headless prompt, use [`a13n-harness-ui run`](command-reference.md#run), which always runs unattended.

## Use the a13n profile

The `initialize` response advertises `dev.a13n.harness-ui` (`version: 1`) under `_meta`. Standard ACP clients can ignore this namespace; profile-aware clients gain inventory discovery, usage reporting, strict steering, and unattended session MCP declarations:

| Message / method                        | Placement / direction                              | Fields and behavior                                                                                                                                                                                 |
| --------------------------------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `initialize`                            | `_meta["dev.a13n.harness-ui"]`                     | Request `{"version": 1}` to opt into `_a13n/usage_report` when context size or occupancy is unknown. Response advertises feature flags (`version`, `unattended`, `steer`, `inputAcceptance`, etc.). |
| `_a13n/inventory`                       | Request (`{"sessionId": "..."}`)                   | Lists `defaultModel`, `models` (ID, name, default/supported reasoning efforts), `skills`, and `mcpServers` (names and source).                                                                      |
| `session/set_config_option`             | Standard ACP request (between turns)               | Sets connection-local `model` and `thought_level` overrides without modifying saved defaults (cleared on resume).                                                                                   |
| `session/prompt`                        | Request `_meta["dev.a13n.harness-ui"]`             | Optional fresh `turnId` (enables `_a13n/steer`) and `inputIds` (one unique ID per content block).                                                                                                   |
| `_a13n/input_accepted`                  | Server notification                                | `{"sessionId", "turnId", "inputId"}` emitted when the input enters the primary model-request checkpoint.                                                                                            |
| `_a13n/steer`                           | Request (`sessionId`, `turnId`, `inputId`, `text`) | Returns `{"delivery": "accepted" \| "not_delivered" \| "unknown"}`. `not_delivered` means that turn did not receive the input; on `unknown` or connection loss, do not replay the input.            |
| `session/update` & `_a13n/usage_report` | Server notifications                               | `usage_update` carries `usageReport` in `_meta["dev.a13n.harness-ui"]` when context size and occupancy are known; otherwise opted-in clients receive `_a13n/usage_report`.                          |
| `session/prompt` result                 | Response `_meta["dev.a13n.harness-ui"]`            | Carries `finalOutput`, `finalOutputOmitted`, and committed `finalMessage` (`continuationId`, `position`), separating the committed final answer from earlier live commentary.                       |

### Pass session-scoped stdio MCP servers

Pass stdio MCP entries in `mcpServers` on `session/new` or `session/resume` (they are connection-local and must be sent again after reconnecting). In `--unattended` mode, explicitly declare servers whose tools need no human input under `_meta["dev.a13n.harness-ui"]` in the same request:

```json
{
  "cwd": "/absolute/path/to/workspace",
  "mcpServers": [
    { "name": "repo-tools", "command": "/absolute/path/to/mcp-server", "args": [], "env": [] }
  ],
  "_meta": {
    "dev.a13n.harness-ui": {
      "version": 1,
      "unattendedSafeMcpServers": ["repo-tools"]
    }
  }
}
```

Undeclared entries in unattended mode do not launch and are reported in `_meta["dev.a13n.harness-ui"].omittedMcpServers` (`[{"name": "...", "reason": "unattended_safety_not_declared"}]`).

Use the [WebUI management MCP endpoint](http-api.md#manage-projects-threads-and-inbox-over-mcp) separately for Project, Thread, and Inbox management.

> [!WARNING]
> The ACP process runs under the configured Harness UI Environment; editor sandbox rules do not apply to it. Supply only trusted stdio MCP commands, as they execute directly on the ACP host.
