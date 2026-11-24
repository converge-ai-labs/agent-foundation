---
title: Connect an ACP client to Harness UI
description: Use Harness UI as an ACP v1 agent in Zed or JetBrains, or launch it from a script without interactive approvals.
---

## Connect your editor

Run the Harness UI ACP command on macOS or Linux. The command is not available on Windows. Set up a Model and Agent with [Harness UI setup](setup.md). In a terminal, run `command -v a13n-harness-ui` to find the executable. Use its absolute path in the editor, especially when a GUI cannot see your terminal's `PATH`.

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

In JetBrains AI Assistant, add a custom ACP agent. For clients that use `~/.jetbrains/acp.json`, the equivalent entry is:

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

Select **Harness UI** in the client and send a short prompt. Keep interactive mode if you want to approve tools in the editor. Harness UI offers **Allow once** and **Reject once**; it does not save a permission change from that choice. To select another configuration file, add `--config`, the absolute YAML path, then `acp` to the argument list.

## Run from another program

Spawn `a13n-harness-ui acp --unattended` with pipes for stdin and stdout. Use an ACP v1 client on those pipes and read diagnostics from stderr. Call `initialize`, then `session/new` with an absolute workspace `cwd`, or `session/resume` with the saved session ID and its first saved root. A resume failure does not start a new session. Follow `session/update` while awaiting `session/prompt`; use `session/cancel` to stop the turn. Check the terminal result before treating streamed text as the final answer.

Unattended mode filters tools that cannot run without human input. Shell remains available where permitted; a command that needs human review fails that tool call. A saved pending decision blocks admission. Reopen that Thread in an interactive surface to resolve it. For a single prompt, use [`run`](command-reference.md#run) instead; `run` is always unattended.

## Use the a13n profile

Read `_meta["dev.a13n.harness-ui"]` from the initialize response to discover profile version 1. Use `_a13n/inventory` to list the session's models, reasoning options, Skills, and MCP names. Set `model` and `thought_level` between turns with `session/set_config_option`. They do not become saved defaults.

For token usage, read `usage_update` when the server knows the context size and occupancy. To receive `_a13n/usage_report` when either is unknown, include `{"version": 1}` in `_meta["dev.a13n.harness-ui"]` in the `initialize` request.

Use a fresh `turnId` in prompt metadata to enable `_a13n/steer`. Optional `inputIds` identify prompt blocks. The `_a13n/input_accepted` notification confirms incorporation at the primary model-request boundary. A steer result of `not_delivered` means that turn did not receive it. If the connection fails or delivery is `unknown`, do not replay that input. The successful prompt result identifies the committed final answer separately from live commentary.

Pass stdio MCP commands in `mcpServers` on new or resume. In unattended mode, supply `version: 1` and `unattendedSafeMcpServers: ["server-name"]` in the a13n namespace of that same request. Declare a server only if its tools need no human input. Check `omittedMcpServers` in the response; an omitted server does not launch. Provide the entries and declarations again after reconnecting.

Use the [WebUI management MCP endpoint](http-api.md#manage-projects-threads-and-inbox-over-mcp) separately for Project, Thread, and Inbox operations such as `post_to_inbox`. ACP sessions receive only the MCP servers selected by their Harness UI configuration and the stdio entries admitted from their own requests.

> [!WARNING]
> The ACP process uses the configured Harness UI Environment; the editor's sandbox rules do not apply to it. Only provide trusted MCP commands. A supplied stdio command runs on the ACP host.
