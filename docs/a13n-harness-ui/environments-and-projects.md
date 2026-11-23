---
title: Environments and Projects
description: Organize multi-root repository Projects, switch between Full Control and Envd OS Sandboxes, and bind remote Envd devices to Harness UI.
---

An **Environment** controls where the Agent reads files and runs commands. A **Project** groups conversations and provides default working directories and settings.

## Select an Environment

Launch Harness UI from your project directory with either execution mode:

```console tab="Full Control"
a13n-harness-ui --environment-mode full-control
```

```console tab="Sandbox"
a13n-harness-ui --environment-mode sandbox
```

- Use `/environment` in the TUI or **Environments** in the WebUI composer to change the mode for upcoming turns.
- Use `--environment-profile <id>` for a custom profile (`a13n-harness-ui environment list --format json`).

## Execution permissions

| Built-in profile      | Runtime and authority                                                  |
| --------------------- | ---------------------------------------------------------------------- |
| `environment-native`  | Direct execution as your Host account (no Envd daemon required)        |
| `environment-envd`    | Full Control through local Envd (Computer Use off)                     |
| `environment-desktop` | Full Control through local Envd with Computer Use enabled              |
| `environment-sandbox` | Envd OS-sandboxed filesystem/process worker (inherits Host networking) |

- **Full Control** inherits your terminal's `PATH`, exported variables, and account permissions.
- **Sandbox** confines writes to Project roots and Thread `tmp/`. Linux and macOS also restrict reads; Windows (experimental MXC) restricts writes while permitting broad local disk reads. Sandbox failures are explicit and never silently fall back to Full Control. See [Envd isolation](../a13n-envd/isolation.md) for OS-specific boundaries.

### Manage local envd

Open **Settings → Environments → Local runtime** (or use the CLI) to install, start, check, or stop the managed Envd binary needed for Sandbox and local Computer Use:

```console
a13n-harness-ui environment status
a13n-harness-ui environment check --profile environment-envd
a13n-harness-ui environment configure --profile environment-envd
```

To enable local Computer Use, turn on **Desktop capability** in **Settings → Environments**, restart Harness UI, and run **Check permissions** on the Host.

## Project file reference

For multi-directory Projects or reusable defaults, save `projects/work.yaml` beside your root config:

```yaml title="projects/work.yaml"
schema_version: "1"
kind: project
id: project-work
name: Work
position: 0
roots:
  - path: /absolute/path/to/code
  - path: /absolute/path/to/notes
```

Validate with `a13n-harness-ui config validate`. The first root is the TUI launch directory and default working directory.

### Defaults for new conversations

Add a `defaults` block to a Project file (or edit **Project settings** in WebUI):

```yaml
defaults:
  agent: agent-reviewer
  environment_profile: environment-native
  harness_plugins: []
  environment_run_extensions: []
  mcp_servers: []
```

Precedence for new conversations: explicit launch/UI choices → Project defaults → Agent defaults → root YAML defaults. Existing conversations keep their saved selections unless you explicitly click **Apply Project defaults**.

## Add Device bindings

Connect another computer via Envd so Agents can work on remote files and commands.

### Connect and approve Envd

1. On the remote computer, run:

```console
a13n-envd connect https://your-webui-host.example.com --host work
```

2. Open the printed approval URL in WebUI and approve the verification code.
3. In **Project settings → Environments** (or **Conversation details → Configuration**), attach the approved Device, select a working directory, and set its allowed operations.

For manual HTTP or reverse-WebSocket device definitions, save a `devices/*.yaml` file using credential references (never raw tokens).

### Revoke a Device

Open **Settings → Environments → Devices** and click **Revoke** to invalidate a Device's credential immediately. Revoking stops new connections; forgetting local configuration removes the saved entry without touching remote files.

## Custom Environment profiles

Create custom profiles under `environment_profiles/*.yaml` (with `schema_version: "1"`, `kind: environment_profile`, `id`, and `name`) to bind cloud sandbox providers (Docker, E2B, Modal, Daytona, Vercel, Sprites) or custom Envd egress/isolation policies. See [Environments overview](../environments/index.md) for provider recipes.
