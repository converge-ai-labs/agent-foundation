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

For a uv-tool installation, run `a13n-harness-ui update` or `uv tool upgrade a13n-harness-ui`, then restart Harness UI. The application command needs uv on PATH and runs the upgrade without another prompt. For other installations, use their original package manager.

Startup can check for updates, but never installs one without confirmation. Disable the check for a single invocation with `--no-update-check`, or permanently with `process.terminal_update_check: false`. See [update and failure behavior](automation-and-troubleshooting.md#logs-updates-and-exit).

### Dependency compatibility

uv resolves the application and dependencies against its published requirements. Harness UI releases independently against compatible Harness, Stream Protocol and Environment dependencies. Upgrade Harness UI itself if an old package's requirements block a dependency update; check any constraints you supplied rather than forcing an incompatible version.

### Sandbox runtime

Full Control needs no Envd download. Sandbox acquires a daemon matching the installed `a13n-envd-client` version when needed, including after an upgrade. Source builds with version `0.0.0` need an explicit validated executable for managed Local EIP. See [Envd installation](../a13n-envd/index.md).

## Run from source

Use the repository's locked environment rather than mixing local code with manually installed dependencies:

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
make a13n-harness-ui
```

Follow the repository [contribution guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md) for development prerequisites. The Make target synchronizes dependencies and disables the published-package startup check. After switching branches, run it again or use `make sync`. After editing UI documentation or navigation, run `make a13n-harness-ui-skills` to refresh the [bundled configuration Skill](skills-and-content-plugins.md#built-in-configuration-skill); the launch target already includes that dependency.

Source documentation tracks `main`. When using a published wheel, consult the documentation and metadata for that release if an API or setting differs.

## Other interfaces

The installed package includes both interfaces. Start the browser workbench with:

```console
a13n-harness-ui webui
```

Open the printed login link to configure models, start conversations, and work with trusted collaborators. The foreground server owns active execution; keep it running while using the browser. Native host file and terminal sharing are enabled by default; use `--no-share-computer` to disable them. See [Use the browser](webui.md) for access controls, collaboration boundaries, containers, and listener configuration.

## Next step

[Complete setup](setup.md), then [find your configuration](configuration.md).
