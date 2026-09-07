# Harness UI

Harness UI (`a13n-harness-ui`, installed from the `a13n-harness-ui` distribution) is an interactive coding CLI built on Agent Foundation Harness. Its native full-terminal interface supports Windows, macOS, and Linux, with reflowing Markdown, selectable interactions, and image drafts. One foreground process owns one `HarnessUiApp`, the current conversation, and its active work. The optional `a13n-harness-ui webui` command starts the HTTP API and a bundled Hello World page in a foreground server; browser chat and management are not implemented. There is no detached daemon or detached execution mode.

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

Startup checks local configuration before opening full-terminal chat. If no Model is configured, a setup wizard opens in the same full-terminal interface before chat. A model request begins only when you explicitly send a prompt. The current directory is the workspace; you do not need to create or manage a Project.

## First use

Setup runs automatically when needed. To change configuration later, leave chat and run `a13n-harness-ui setup`; there is no `/setup` command inside chat.

1. **Connect a model:** choose Codex subscription, Grok subscription, or an API key. Existing compatible Codex/Grok logins are detected and reused without another login prompt, including credentials that can refresh when used. API-key access asks for a model route and an environment-variable or stored-key reference, never the raw key.
2. **Choose execution permissions:** Full Control runs as your host account; Sandbox uses isolated execution and checks prerequisites before saving. There is no automatic fallback between them.
3. **Include default subagents:** choose all three built-in roles (`code-reviewer`, `executor`, `explorer`) or none. Only an inclusion list is saved; definitions stay in the installed package. You can [select individual names later](agents-and-subagents.md#built-in-subagents).
4. **Review and save:** confirm the connection, starter settings, workspace, permissions, and files to publish. Codex defaults to Sol, high reasoning, a 350k working budget, and enabled shell review. Choose **Adjust model options** only if you want to change the model, context budget, reasoning, shell review, or additional instructions. Existing edited resources are preserved.

Use Up/Down and Enter, or type option numbers. Esc goes back; Ctrl+C or Ctrl+D cancels. Cancelling first-use setup returns to the command shell without opening chat. After successful first-use setup, chat opens automatically. Running `a13n-harness-ui setup` explicitly returns to the command shell after saving or cancelling.

If the selected account is missing, setup shows the external `a13n-harness-ui login codex` or `a13n-harness-ui login grok` command, then offers recheck or configuration without signing in. Login runs outside the terminal UI; chat has no `/login` command. Unsupported or malformed stores show repair guidance and a recheck action; they are not overwritten. Discovery never refreshes tokens or starts authentication. Cancelling setup does not undo a completed login or configuration publication.

External subagent migration is separate from built-in inclusion: use `/import` in chat to select Codex or Claude Code definitions, project/user scope, and an explicit import-and-enable confirmation. Setup does not scan or import external definitions.

Imports preserve instructions and explicitly inherit the parent model and visible tools rather than activating foreign tool names. Preview lists unsupported settings and conflicts. Successful import enrolls selected definitions in the selected Agent's roster; file publication and enrollment are separate operations, and partial completion is reported for deliberate retry.

Setup creates editable YAML resources. It does not put OAuth tokens or API keys into them, call a model to test entitlement, or silently overwrite edited Model resources. A preserved existing Model keeps its existing settings even if you selected different starter values; edit its YAML to change those values. Explicitly connecting the selected Agent can update its model binding through the reviewed publication.

## Find your next step

| I want to…                                                      | Guide                                                               |
| --------------------------------------------------------------- | ------------------------------------------------------------------- |
| Send prompts, steer work, attach images, and handle decisions   | [Use the terminal](everyday-use.md)                                 |
| Find every root setting and understand file precedence          | [Configuration reference](configuration.md)                         |
| Use a subscription or API key; tune reasoning and context       | [Models and authentication](models-and-authentication.md)           |
| Create an Agent file or reference an existing Agent as a child  | [Agents and subagents](agents-and-subagents.md)                     |
| Choose execution permissions and configure multiple directories | [Environments and Projects](environments-and-projects.md)           |
| Add MCP servers, Skills, or plugins                             | [Extensions and MCP](extensions-and-mcp.md)                         |
| Use one-shot commands, recover work, or diagnose failures       | [Automation and troubleshooting](automation-and-troubleshooting.md) |
