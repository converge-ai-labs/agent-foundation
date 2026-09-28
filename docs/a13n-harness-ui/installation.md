# Installation and upgrades

## Install Harness UI

Use [uv](https://docs.astral.sh/uv/getting-started/installation/) to keep Harness UI in an isolated tool environment:

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

No source checkout or Node.js is required. If your shell cannot find the command, run `uv tool update-shell` and restart the shell.

For an optional Bash/Zsh shortcut, add this to your shell profile:

```bash
alias anui='a13n-harness-ui'
```

This is only a shell alias. Documentation and automation use the installed name, `a13n-harness-ui`.

## Upgrade

For a uv-tool installation, either command requests an upgrade:

```console
a13n-harness-ui update
```

```console
uv tool upgrade a13n-harness-ui
```

Restart Harness UI afterwards. The application command upgrades the running uv-tool installation; it requires uv on PATH and does not ask for another confirmation. If another package manager owns your installation, use that package manager rather than modifying an unrelated environment.

Startup can check for updates, but never installs one without confirmation. Disable the check for a single invocation with `--no-update-check`, or permanently with `process.terminal_update_check: false`. See [update and failure behavior](automation-and-troubleshooting.md#logs-updates-and-exit).

### Dependency compatibility

uv resolves the application and dependencies together using the installed package's requirements. Harness, Environment, and Stream Protocol share one exact release version; Harness UI releases independently against compatible dependency ranges.

An upgrade cannot widen requirements embedded in an older wheel. If an old installation pins dependencies, upgrade Harness UI itself. If you supplied an installation constraint, review that constraint rather than forcing an incompatible dependency into the tool environment. Use the installed package metadata and release notes for its actual bounds; source workspace packages use version `0.0.0` and are not published release artifacts.

### Sandbox runtime

Full Control does not download or start Envd. Managed Local EIP acquires the native daemon matching the **installed `a13n-envd-client` version**, when needed. It does not search for the newest daemon or silently upgrade Python packages.

After a package upgrade and restart, the next Local EIP use acquires the matching daemon if it is not cached. Source version `0.0.0`, missing metadata, or invalid metadata cannot select a managed native release; source development needs an explicit validated executable. See [Envd installation](../a13n-envd/index.md).

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
