# Agent UI

Agent UI is a local, single-user conversation application for Agent Foundation Harness. Choose the terminal or explicitly start the browser server:

```console
a13n-ui
a13n-ui webui
```

One foreground process owns one App and its active root and child work. There is no separate agent daemon, IPC service, or detached execution mode. Different conversations can run concurrently in that process. Switching conversations does not cancel work; exiting the terminal or stopping the WebUI server ends its App lifetime. Closing a browser tab only closes that tab's delivery.

## First-use setup

On an empty installation, the terminal and browser offer setup before your first conversation. Reopen it with `a13n-ui setup`, the terminal `/setup` command, or the browser Setup action.

1. **Model connection.** Choose API key (BYOK) or subscription (BYOS), or **Not now**. BYOK configures a model route and a host environment-variable reference; it does not store a key. BYOS discovers compatible Codex/Grok login, with all available providers initially selected. Discovery makes no login, refresh, or model request.
2. **Execution environment.** Confirm a new Project's directory or reuse existing roots. Sandbox must pass its production readiness check for every root before Continue is enabled. Retry or cancel a failed check, or explicitly choose Full Control to run as your host user without isolation. This step works even if you skipped model connection.
3. **Your Agent.** A default Agent is already selected. Choose model-specific options and optional additional instructions, inspect the proposed files, then **Finish setup**. Back preserves your choices. Preview writes nothing, and only Finish setup publishes files.

**Not now** is not Cancel setup: it lets you complete the remaining steps with a default Agent that has no Model yet. You can enter the conversation shell and keep a draft, but that Agent cannot execute until a Model is configured. Reopen Setup to choose a connected starter or existing Agent; existing Agent files are preserved rather than silently rebound. Restarting does not reopen onboarding just because you deferred the connection.

The subscription step currently reuses external login, not an embedded authorization flow. If no compatible login exists, complete an explicit login in another terminal on the server host, then use Refresh accounts:

```console
a13n-ui auth login codex
a13n-ui auth login grok
```

Do not paste OAuth tokens into setup. Login uses the compatible provider store. A different shared account requires the normal explicit account-switch confirmation. Availability is not a guarantee that every model is entitled to your subscription.

### Starter Agents

The September 2026 starter choices are editable defaults:

| Provider | Main model                   | Reasoning         | Default shell reviewer                                   |
| -------- | ---------------------------- | ----------------- | -------------------------------------------------------- |
| Codex    | `openai-codex:gpt-5.6-terra` | medium            | `openai-codex:gpt-5.6-luna`, low thinking                |
| Grok     | `grok:grok-4.6`              | provider defaults | Codex Luna if Codex is also selected; otherwise Grok 4.6 |

Codex setup also offers Sol for deeper reasoning and Astra where the account has access. Grok-only setup does not assume a cheaper compatible subscription route. Shell review starts enabled for subscription setup; flagged commands and review failures request approval. Review is not a sandbox.

Starter Agents enable file/shell tools and project-aware skills. Every Agent receives a release-owned system prompt for evidence-based work, preserving user changes and authority, verification, and accurate reporting. The optional `instructions` field adds preferences or task guidance through Pydantic AI's separate instructions channel; it never replaces the built-in system prompt. Omitting instructions still produces a fully instructed Agent. Both layers are frozen for each Run; changing defaults cannot rewrite old captures. Setup does not silently enable external MCP servers, task tools, or a child roster. Add those through the ordinary editable configuration when needed. Only WebUI roots receive the host-owned Thread collaboration tools; this is not enabled by editing starter YAML.

### API-key configuration

The API-key step takes a supported `provider:model-name` route and the name of an environment variable, for example `OPENAI_API_KEY`. The variable must exist in the **Agent UI server process**, not just the browser or another terminal. Set it using your normal local secret-management mechanism before launching Agent UI. Do not paste the key into either field. Direct Key entry/storage and in-wizard subscription authorization remain unimplemented; a completed wizard is not proof that credentials or model access work.

### Files and recovery

Find the selected configuration root with:

```console
a13n-ui config path
a13n-ui config validate
```

On Unix-like systems the default root is `~/.a13n-ui/a13n-ui.yaml`; `--config PATH` selects another tree. Models, Agents, and Projects are ordinary YAML files in sibling `models/`, `agents/`, and `projects/` directories. Resources are created once, and existing edits are not overwritten by a later setup or upgrade. New defaults affect future conversations, not existing ones.

Publication creates resources first and updates root defaults last. A multi-file filesystem update is not a transaction: a failure may leave completed files, and the result reports those paths. Review them and preview again. Do not assume closing a tab or a failed response rolled publication back. Browser navigation is temporarily blocked while Apply is pending.

When updating an existing root, setup temporarily retains it in a private `.a13n-ui-setup-recovery-*` directory beside the configuration file. A crash or competing save can leave the retained original there. Further setup publication stops until you inspect both versions and restore or move the retained original. Do not delete the recovery file without inspecting it. Setup never deletes a competing save to force its own version into place.

## Sandbox or Full Control

**Full Control** runs as your host account with ambient filesystem and network access. It does not download or start agent-envd, alter system policy, or provide isolation. Windows uses PowerShell for the default shell profile.

**Sandbox** resolves the configured or release-managed agent-envd executable and runs the same exact-version and isolation checks used in production preparation. It checks filesystem, process, and denied-network isolation rather than merely finding a `bwrap` command. The check is bounded and cancellable; each Run still validates its actual environment.

If readiness fails, choose one of:

- **Retry** after following the linked prerequisite instructions;
- **Cancel** to retain your previous selection and unsent prompt;
- **Choose Full Control (no Sandbox)** to explicitly run without isolation.

There is no automatic downgrade. Agent UI never runs `sudo`, disables required isolation, changes sysctls, or installs system security policy for you. Windows production Sandbox isolation is not supported; choose Full Control explicitly if appropriate. On Linux, including Ubuntu's unprivileged-user-namespace/AppArmor restrictions, follow the [agent-envd operations guide](agent-envd/index.md#isolation-behavior).

The terminal checks Sandbox when you select it and before a first submission using an unchecked Sandbox default. Choosing Full Control during recovery changes the selection but does not automatically send the waiting prompt. The browser checks Sandbox for new-conversation selections and during setup.

## Conversation workflow

### Terminal

The terminal has one conversation and one composer, not a Workbench:

- `Ctrl+O` opens the transient Thread picker. Search by title or ID, select the current Project or All Projects, and load bounded pages.
- `Ctrl+N` starts a new draft without creating a Thread.
- `Ctrl+P` opens the command palette; `/threads`, `/setup`, `/agent`, and `/environment` are also available from the composer.
- `Escape` closes a transient surface without clearing the draft or approving a decision.
- Ordinary input while a root Run is active steers that exact Run. It is never queued as a hidden next message.

Only the selected Thread has a detailed live subscription. Other active conversations continue and retain their own unsent drafts. Exiting with active work requires confirmation; work does not detach after shutdown.

### Browser

Open the URL printed by `a13n-ui webui`. The server defaults to `127.0.0.1:8765`. It prints a fresh process-local key and a convenience URL containing that key in the fragment. The browser removes the fragment immediately and keeps the key in that tab's session storage. A supplied `--api-key` is not echoed; enter it in the access form.

The header provides New conversation, Conversations (`Ctrl+O`), and Setup. The modal picker is navigation only: no second composer, split preview, or per-row execution controls. Drafts, pending submissions, and unknown-outcome notices survive conversation switches in page memory but are not stored across page reload or sign-out.

The first Send creates a Thread and then admits the message. These are separate operations. If admission fails, the created Thread and prompt remain available. If the response is lost, review authoritative state before explicitly enabling another attempt; the browser never retries a command automatically. A rejected steer preserves the prompt. Text typed while a request is pending is not erased by the older response.

Retained conversation history is separate from the bounded provisional live tail. Switching back can show recent current-process events but cannot recover already-evicted live output. Receipt state and retained continuations, not a terminal-looking stream event, decide completion. Approvals and questions submit against the exact selected continuation.

This browser implementation provides conversation execution and setup. General-purpose guided Settings resource editors and advanced child-control screens are not included; use editable resource files and the existing CLI management commands for those operations.

### Listener exposure

`--host` and `--port` change the foreground listener. Non-loopback HTTP is still a single-user listener, not a multi-user deployment boundary. Protect remote access appropriately; API keys do not encrypt plain HTTP.

`--dangerously-bypass-permission` disables API authentication for every reachable client. It is not required for normal local use and cannot be combined with `--api-key`. Disconnecting one browser does not stop server-owned work. Restarting the server does not automatically reacquire old receipts or replay interrupted input.
