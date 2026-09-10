# Environments and Projects

## Execution permissions

**Full Control** runs as your host account with ambient filesystem and network authority. It does not download or launch a13n-envd. Direct Local executes natively on Linux, macOS, and Windows without WSL. Windows uses PowerShell (`pwsh`, then Windows PowerShell), UTF-8 text streams, and a Job Object that owns the command's descendants. Cancellation, timeout, and exit clean up that owned tree. Portable interrupt signals are unavailable on Windows; cancellation uses tree termination instead. Full Control remains host-account execution, not a sandbox.

**Sandbox** uses the local Environment provider and required filesystem/process isolation with denied networking. Setup checks Sandbox readiness before saving. Outside setup, readiness is checked when execution needs it rather than on the initial landing view. A failure is explicit and does not fall back to Full Control. Fix the prerequisite and retry, or intentionally select `/environment full-control` before sending a new prompt. Harness UI never runs `sudo`, changes sysctls, or disables required isolation for you. Windows production Sandbox isolation is not supported. See the [a13n-envd operations guide](../a13n-envd/index.md#isolation-behavior).

### Native command environment

Full Control commands inherit the complete environment of the Harness UI process, including `PATH`, proxy settings, tool-specific variables, and exported credentials. A command's explicit environment overrides or removals apply only to that command and its descendants; they do not change Harness UI or later commands. This applies to both Project and Thread-file native shell execution. Environment values are not copied into saved configuration or Run snapshots.

Launch Harness UI from a terminal where your tools and variables are already available. Changes made in another terminal after launch are not automatically reflected in the running process. Environment inheritance does not include aliases, unexported shell variables, or automatic sourcing of `.zshrc`/`.bashrc`. The TUI's `!command` also inherits the Host process environment. Sandbox keeps its separate isolation and environment policy.

## Windows Local Execution

Windows supports **Full Control only** for the built-in local modes. Setup and the CLI Environment selector offer Full Control and explain that commands run with the Host account's filesystem and network permissions. Job Object cleanup is not Sandbox isolation. Explicit Sandbox requests fail without downloading envd, changing saved selections, or falling back. Custom and remote Providers retain their own contracts.

## Files, Projects, and recovery

Model, Agent, extension, MCP, and Project resources live in sibling YAML directories. A Project contains an ordered list of directories; the first is its default working directory. Launching the CLI in that first directory uses the same Project and all its roots. For example, a Project with roots `[code, notes]` is entered from `code`; adding `notes` later does not create a new Project or hide existing CLI sessions.

The first prompt creates a single-root Project only when no Project's first directory matches. It never adopts a parent Project, treats a secondary root as another entry point, or rewrites an existing Project. Multiple matching Projects require resuming a specific session or editing their roots. To retain a conversation's workspace, launch the CLI in its Project's first directory. An explicit resume from another directory reassigns the Thread to the launch directory's Project for future turns while preserving its history and other selections. `--resume` cannot be combined with Agent, Environment, or title overrides; resume first, then use an explicit slash command.

Configuration publication failures belong to [setup recovery](setup.md#cancel-or-recover-setup), not Project selection. Review completed paths and retained recovery files before retrying setup.

Process loss discards active receipts and incomplete input/output. Resume continues the last saved checkpoint (which can contain safely retained partial progress); it does not replay interrupted side effects. Best-effort live output can be incomplete; if events are lost, the terminal labels recovery and prints the authoritative final answer.

## Select an Environment

```console
a13n-harness-ui environment list --format json
a13n-harness-ui --environment-mode full-control
a13n-harness-ui --environment-mode sandbox
a13n-harness-ui --environment-profile environment-team
```

`--environment-mode` selects a built-in mode; `--environment-profile` selects a configured profile. They are mutually exclusive. Inside chat, `/environment` shows choices and changes the next operation's selection. Resume first before changing a saved session's Environment; launch overrides cannot be combined with `--resume`.

## Project file reference

For a stable multi-directory workspace, create `projects/work.yaml` beside root YAML. Replace both paths with existing directories:

```yaml
schema_version: "1"
kind: project
id: project-work
name: Work
position: 0
roots:
  - path: /absolute/path/to/code
  - path: /absolute/path/to/notes
```

| Field            | Default            | Meaning                                       |
| ---------------- | ------------------ | --------------------------------------------- |
| `schema_version` | Required `"1"`     | Resource schema                               |
| `kind`           | Required `project` | Resource type                                 |
| `id`             | Required           | Unique stable `project-` identity             |
| `name`           | Required           | Human-facing name                             |
| `position`       | `0`                | Catalog ordering                              |
| `roots`          | Required           | 1–64 ordered unique `{path: ...}` directories |

Paths must be absolute after `~` expansion and must exist. The first is the default working directory and terminal entry point. Changing a root list changes later captures, not an active Run. Project roots organize work and Environment mounts; they do not confine Full Control's host authority.

## Custom Environment profiles

Most users should select a built-in mode rather than copy its configuration. Reserved IDs `environment-native` and `environment-sandbox` cannot be redefined.

Custom profiles live under `extensions/` and select an installed provider plus a compatible Project adapter. For example, this native profile still runs with host authority:

```yaml
schema_version: "1"
kind: environment_profile
id: environment-team
name: Team native environment
provider_key: a13n.direct-local
provider_schema_version: "1"
provider_configuration: {}
adapter_key: a13n.native-project-root
adapter_configuration: {}
```

Beyond the common resource envelope, all fields are shown above. The provider owns `provider_schema_version` and `provider_configuration`; the adapter owns `adapter_configuration` and Project-to-Environment mapping. Empty mappings are defaults, not a universal configuration for every provider. A provider package alone does not imply that every Project adapter is installed or compatible.

Use `defaults.environment_profile: environment-team` or the explicit launch option, then run `config validate` and `doctor`. Never treat a profile label as proof of isolation. Provider readiness and the actual execution contract determine that behavior.
