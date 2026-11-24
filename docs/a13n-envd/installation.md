---
title: Install Envd
sidebarTitle: Installation
description: Download verified a13n-envd release binaries, choose between standard, slim, desktop, and VM flavors, or build from Cargo source.
---

Install `a13n-envd` on any machine that will execute file, command, or desktop operations. Harness UI automatically downloads its matching binary for local Sandbox mode; use this guide when registering a computer as a remote Device or managing `a13n-envd` directly.

## Choose a binary flavor

All six Linux, macOS, and Windows release targets publish `slim` and `default` builds, and Linux additionally publishes `vm`. Every flavor speaks the same EIP schema (inspect an installed binary with `a13n-envd --build-variant`):

| Flavor       | Included capabilities                                                                                                | Archive suffix |
| ------------ | -------------------------------------------------------------------------------------------------------------------- | -------------- |
| `slim`       | Files, Shell/processes, transfers, native isolation & egress, transports/TLS, monitoring/export, enrollment, updater | `-slim`        |
| `default`    | `slim` plus native desktop screenshots, input, clipboard, and accessibility                                          | None           |
| `vm` (Linux) | `default` plus embedded microVM backends                                                                             | `-vm`          |

Installers default to `default`; pass `--variant slim` (`A13N_ENVD_VARIANT=slim`) for headless servers or `--variant vm` (`A13N_ENVD_VARIANT=vm`) on Linux when using microVM isolation.

## Build the matching binary

When developing from source, build `a13n-envd` from the same checkout as `a13n-envd-client` so their versions match:

```bash
cargo build --locked --package a13n-envd
export A13N_ENVD_EXECUTABLE="$PWD/target/debug/a13n-envd"
```

Use `--no-default-features` for a `slim` build, or `BOXLITE_DEPS_STUB=2 BOXLITE_EMBED_RUNTIME=1 cargo build --locked --package a13n-envd --features vm` on Linux (with a C compiler and `protoc >= 3.12`) for a `vm` build.

## Connect this computer from Console

Register your workstation with a Service workspace under **Console → Environments → Instances → Connect my computer**:

- **macOS launcher app (source checkout):** Build the binary above, run `make envd-app-install` (or `ENVD_APP_ARGS=--replace` when upgrading) to install `~/Applications/A13N Envd.app`, click **Connect my computer**, choose a working directory and optional desktop access in the app, and enter the 6-digit pairing code in Console. Grant macOS Screen Recording and Accessibility permissions if enabling desktop use.
- **Terminal pairing (Linux, Windows, or macOS without launcher):** Choose **Connect from a terminal** in Console and run the copied `a13n-envd connect-service --pairing-id ...` command within 10 minutes (`A13N_ENVD_FULL_CONTROL=1` enables unrestricted Shell; `--computer-use true` enables desktop control). Reconnect saved profiles anytime with `a13n-envd connect-service <saved-host-name>`.

## Install a published binary

Match the native `a13n-envd` version to your installed Python [`a13n-envd-client`](python-client.md#install) (`1.2.3rc1` maps to `1.2.3-rc.1`). Both installers verify `SHA256SUMS` before atomic extraction.

### Linux and macOS

```bash
curl -fsSLo install-a13n-envd.sh \
  https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.sh
ENVD_VERSION="$(python -c 'from importlib.metadata import version; import re; print(re.sub(r"rc([0-9]+)$", r"-rc.\1", version("a13n-envd-client")))')"
sh install-a13n-envd.sh --version "$ENVD_VERSION" --install-dir "$HOME/.local/bin"
"$HOME/.local/bin/a13n-envd" --version
```

### Windows (PowerShell)

```powershell
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.ps1' -OutFile install-a13n-envd.ps1
$envdVersion = python -c "from importlib.metadata import version; import re; print(re.sub(r'rc([0-9]+)$', r'-rc.\1', version('a13n-envd-client')))"
& .\install-a13n-envd.ps1 --version $envdVersion --install-dir "$env:LOCALAPPDATA\A13N\bin"
& "$env:LOCALAPPDATA\A13N\bin\a13n-envd.exe" --version
```

| Installer flag                       | Environment variable            | Behavior                                                                                   |
| ------------------------------------ | ------------------------------- | ------------------------------------------------------------------------------------------ |
| `--version X.Y.Z`                    | `A13N_ENVD_VERSION`             | Exact native version (omit to install the latest published stable pointer)                 |
| `--install-dir PATH`                 | `A13N_ENVD_INSTALL_DIR`         | Target directory (`~/.local/bin`, `/usr/local/bin` for root, or `%LOCALAPPDATA%\A13N\bin`) |
| `--variant default\|slim\|vm`        | `A13N_ENVD_VARIANT`             | Archive flavor (`default` unless overridden)                                               |
| `--add-to-path` / `--no-add-to-path` | `A13N_ENVD_ADD_TO_PATH=1` / `0` | Opt into adding the install directory to the current user's shell/profile `PATH`           |

## Update an existing installation

Stop running `a13n-envd` daemons using that binary first so worker spawns never mix versions, then run the built-in updater:

```console
a13n-envd update --check
a13n-envd update
# Pin a specific client-matched version, reinstall, or switch flavors:
a13n-envd update --version 1.2.3
a13n-envd update --version 1.2.3 --variant slim
```

After updating on macOS, follow [Restore macOS permissions](computer-use.md#restore-macos-permissions-after-an-upgrade) if Screen Recording or Accessibility prompts reappear. For containerized workloads, see the [Sandbox image](sandbox.md) (`<version>`, `<version>-vm`, `<version>-slim-vm`, `<version>-desktop-vm`), [Daemon configuration](configuration.md), and [Execution boundaries](isolation.md).
