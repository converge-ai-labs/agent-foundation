---
title: Installation and upgrades
description: Install the terminal and browser workbench, verify the command, and upgrade an existing installation.
---

Install the published package for normal use. It includes both terminal and browser interfaces; you need neither a source checkout nor Node.js. Have [uv](https://docs.astral.sh/uv/getting-started/installation/) installed before continuing.

## Install Harness UI

1. Install Harness UI in its own tool environment:

   ```console
   uv tool install a13n-harness-ui
   ```

2. Check that your shell can find the command:

   ```console
   a13n-harness-ui --help
   ```

   You should see the command's usage and subcommands. If the shell reports that the command is missing, run `uv tool update-shell`, restart the shell, and try again.

3. Open the directory you want the Agent to work in, then launch:

   ```console
   cd your-repository
   a13n-harness-ui
   ```

   Replace `your-repository` with your own directory. On first launch, follow [Set up your first Agent](setup.md). That guide ends with a test message so you can verify the model connection separately from file and shell tools.

## WebUI

To use the same installation from a browser, run:

```console
a13n-harness-ui webui
```

Open the printed login link and leave the foreground server running. WebUI has its own guided setup. Native Host file and terminal sharing are enabled by default; start with `a13n-harness-ui webui --no-share-computer` to disable them. See [WebUI](webui.md) before sharing access with others.

### Browser Use prerequisites

The WebUI itself opens in your browser. The optional **Browser** drawer launches a separate browser on the Linux or macOS WebUI Host for the Agent to use.

Prepare the Host for your platform:

- **Linux:** install Chrome, Chromium, or Edge and a compatible VNC server. For a container deployment, install them inside the container. TigerVNC supplies both the display and VNC server. Run as a non-root user with Chromium's sandbox enabled.
- **macOS:** install a compatible browser and run Harness UI as the logged-in desktop user. Optional read-only preview needs macOS 14 or later and Screen Recording permission for the application running Harness UI. It shows only the conversation's owned browser window, including the address bar. Use **Pause automation** before operating the browser locally on the Mac, then **Resume automation**. No VNC service or password is needed.

The default Auto mode prepares the shared browser when WebUI starts. Ask a Full Control Run to browse, or choose **Browser → Open browser**. Harness UI automatically selects compatible installed programs; use **Settings → General → Host browser** to change the Auto/Disabled mode or override program selection. Follow the [browser setup procedure](webui.md#first-time-setup) for platform steps and a first navigation. Harness UI already includes the browser automation dependencies. Keep computer sharing enabled: `--no-share-computer` also disables the Host browser.

## Upgrade

Upgrade all installations sharing a data root together:

1. Stop their TUI and WebUI processes.

2. For a uv-tool installation, run:

   ```console
   a13n-harness-ui update
   ```

   This requires `uv` on `PATH` and proceeds without another prompt. `uv tool upgrade a13n-harness-ui` is equivalent; for other installation methods, use the original package manager.

3. After every shared installation has been upgraded, restart the applications and reload open WebUI pages.

The upgraded application reads existing conversations and writes the current checkpoint format. Older applications cannot read those newly written checkpoints, so keep shared installations on the same upgraded version.

Startup update checks only display a notice. To turn the check off for one invocation, use `--no-update-check`; for later starts, set `process.terminal_update_check: false` in the root YAML. See [updates and logs](automation-and-troubleshooting.md#logs-updates-and-exit).

### Dependency compatibility

Let the package manager upgrade Harness UI and resolve its compatible dependencies together. These include Harness, Stream Protocol, Envd Client, and logging. If resolution fails, inspect any version constraints you added rather than upgrading those dependencies independently.

### Sandbox runtime

Full Control needs no Envd download. Sandbox acquires the daemon version matching the installed `a13n-envd-client` when needed, including after an upgrade. For a source build whose version is `0.0.0`, provide an explicit, validated `a13n-envd` executable; see [Envd installation](../a13n-envd/installation.md).

## Run from source

Use this route when developing Harness UI or reproducing documentation against `main`. First install the development prerequisites in [CONTRIBUTING.md](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md), then run:

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
make a13n-harness-ui
```

The Make target synchronizes the locked dependencies, rebuilds the [bundled configuration Skill](skills-and-content-plugins.md#built-in-configuration-skill), and launches without startup update checks. Repeat the target after switching branches. To rebuild only the Skill after editing documentation, run `make a13n-harness-ui-skills`.

These documentation sources track `main`. If a published release differs, use that release's documentation and metadata for its supported settings.

## Next step

[Set up your first Agent](setup.md), connect your model account, and verify a response.
