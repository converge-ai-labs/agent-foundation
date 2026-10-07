---
title: Installation and upgrades
description: Install, upgrade, or run Harness UI from source.
---

## Install Harness UI

Use [uv](https://docs.astral.sh/uv/getting-started/installation/) to keep Harness UI in an isolated tool environment:

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

No source checkout or Node.js is required. If your shell cannot find the command, run `uv tool update-shell` and restart the shell.

Optional Bash/Zsh shortcut: `alias anui='a13n-harness-ui'` in your shell profile.

## Upgrade

For a uv-tool installation, run `a13n-harness-ui update` or `uv tool upgrade a13n-harness-ui`, then restart Harness UI. `a13n-harness-ui update` needs uv on PATH and runs the upgrade without another prompt. For other installations, use their original package manager.

Startup can check for updates, but never installs one without confirmation. Disable the check for a single invocation with `--no-update-check`, or permanently with `process.terminal_update_check: false` in `a13n-harness-ui.yaml`. See [update and failure behavior](automation-and-troubleshooting.md#logs-updates-and-exit).

### Dependency compatibility

Upgrade Harness UI as a package; uv resolves compatible Harness, Stream Protocol, Envd Client, and logging dependencies. If resolution fails, review your version constraints.

### Sandbox runtime

Full Control needs no Envd download. Sandbox acquires a daemon matching the installed `a13n-envd-client` version when needed, including after an upgrade. Source builds with version `0.0.0` need an explicit, validated `a13n-envd` executable for Sandbox. See [Envd installation](../a13n-envd/index.md).

## Run from source

Use the repository's locked environment rather than mixing local code with manually installed dependencies:

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
make a13n-harness-ui
```

The Make target synchronizes dependencies, refreshes the [bundled configuration Skill](skills-and-content-plugins.md#built-in-configuration-skill), and disables startup update checks. Run it again after switching branches. To refresh only the Skill after documentation edits, use `make a13n-harness-ui-skills`. Development prerequisites are in [CONTRIBUTING.md](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md).

Source documentation tracks `main`. When using a published wheel, consult the documentation and metadata for that release if an API or setting differs.

## WebUI

The installed package includes both the TUI and WebUI. Start WebUI with:

```console
a13n-harness-ui webui
```

Open the printed login link and keep the foreground server running. Configure Models, start conversations, and work with trusted collaborators. Native host file and terminal sharing are enabled by default; use `--no-share-computer` to disable them. See [WebUI](webui.md) for access and deployment.

## Next step

[Complete setup](setup.md), then [find your configuration](configuration.md).
