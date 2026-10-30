---
title: Install Envd
sidebarTitle: Installation
description: Build or download an Envd binary that matches your EIP client, or run the sandbox image.
---

Install Envd on the computer that will execute file, command, or desktop operations. Harness UI acquires its own matching binary for local Sandbox mode. Use this guide when connecting another computer as a Device or when your application manages Envd directly.

## Build the matching binary

For repository development, build the daemon and Python client from the same checkout. These pages track `main`; Local Envd checks that their release versions match.

1. Install Rust and build Envd from the repository root.
2. In the same shell, set `A13N_ENVD_EXECUTABLE` to the resulting binary.

```bash
cargo build --locked --package a13n-envd
```

Select the built executable:

```bash
export A13N_ENVD_EXECUTABLE="$PWD/target/debug/a13n-envd"
```

The build produces `target/debug/a13n-envd`. Your Provider or Host launches it and chooses the transport; `a13n-envd-client` supplies the Python protocol client.

For a published Local Envd installation, match the native release to the installed `a13n-envd-client`, which is versioned independently of Harness and Harness UI. Python RC version `1.2.3rc1` maps to native `1.2.3-rc.1`.

## Install a published binary

The standalone scripts install the native executable from GitHub Releases. To select a version for a Python application, first install the [Python client](python-client.md#install) in the environment that will use Envd. The commands below read that installed package's version.

### Linux and macOS

Prepare a POSIX shell, `curl`, `tar`, and `sha256sum` or `shasum`. Automatic stable-version selection also needs `jq`; the explicit version below does not.

1. Select the Python environment containing your published client.
2. Download and inspect the installer before running it.
3. Read the client version, install the matching executable, and check `--version`.

```bash
curl -fsSLo install-a13n-envd.sh \
  https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.sh
# Inspect install-a13n-envd.sh before running it.
ENVD_VERSION="$(python -c 'from importlib.metadata import version; import re; print(re.sub(r"rc([0-9]+)$", r"-rc.\1", version("a13n-envd-client")))')"
sh install-a13n-envd.sh --version "$ENVD_VERSION" --install-dir "$HOME/.local/bin"
"$HOME/.local/bin/a13n-envd" --version
```

### Windows

Use Windows PowerShell 5.1 or PowerShell 7.

1. Select the Python environment containing your published client.
2. Download and inspect the installer.
3. Install the matching version and check the executable at the printed path.

```powershell
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.ps1' -OutFile install-a13n-envd.ps1
# Inspect the downloaded script before running it.
$envdVersion = python -c "from importlib.metadata import version; import re; print(re.sub(r'rc([0-9]+)$', r'-rc.\1', version('a13n-envd-client')))"
& .\install-a13n-envd.ps1 --version $envdVersion --install-dir "$env:LOCALAPPDATA\A13N\bin"
& "$env:LOCALAPPDATA\A13N\bin\a13n-envd.exe" --version
```

Run scripts according to your organization's PowerShell execution policy; the installer does not change that policy.

### Options and behavior

Choose an exact version and destination with these options. Both installers use the same names:

| Option                               | Environment default             | Behavior                                                                                                                                                                                                                 |
| ------------------------------------ | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--version X.Y.Z`                    | `A13N_ENVD_VERSION`             | Exact native version; canonical RCs such as `0.0.6-rc.1` require explicit selection. Omit both to select the first stable Envd release in GitHub's newest-first release list, skipping other components and prereleases. |
| `--install-dir PATH`                 | `A13N_ENVD_INSTALL_DIR`         | Absolute destination directory. Defaults to `~/.local/bin` for POSIX users, `/usr/local/bin` for POSIX root, or `%LOCALAPPDATA%\A13N\bin` on Windows.                                                                    |
| `--add-to-path` / `--no-add-to-path` | `A13N_ENVD_ADD_TO_PATH=1` / `0` | Mutually exclusive. Default is no PATH modification. Explicit flags override environment values.                                                                                                                         |
| `--help`                             | —                               | Show usage without installing.                                                                                                                                                                                           |

PATH opt-in updates only the current user's configuration: Bash `.bashrc` (`.bash_profile` on macOS), Zsh `${ZDOTDIR:-$HOME}/.zshrc`, POSIX shell `.profile`, Fish `${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish`, or Windows user `Path`. Re-running does not append the same entry again. Open a new terminal afterward. Unsupported shells can use `--no-add-to-path` and configure PATH manually.

The installers support x86_64 and ARM64 on Linux, macOS, and Windows. They verify the selected archive against the release's `SHA256SUMS` **before extraction**, stage the executable on the destination filesystem, and atomically install or replace it. Download, checksum, archive, or replacement failures leave an existing executable unchanged. SHA256 verifies the archive against the manifest delivered by GitHub; it is not a separate signature or trust source.

## Update an existing installation

Use the installation owner's account and update during a stop/restart window. Envd starts workers from its executable path, so replacing a running daemon's binary can mix versions or prevent worker startup.

1. Check the selected version and destination with `update --check`.
2. Stop every daemon using that executable.
3. Run the update, choosing an exact version below when needed.
4. Verify `a13n-envd --version`, then restart Envd with its usual command or service.

```console
a13n-envd update --check
a13n-envd update
```

The default selects the newest stable **Envd** release, skipping other repository components and prereleases. To match a client, install an RC, or explicitly downgrade, select an exact published version:

```console
a13n-envd update --version 1.2.3
a13n-envd update --version 1.2.3 --force
```

`--force` reinstalls the selected version and cannot be combined with `--check`. The updater replaces the actual executable you invoked, including when reached through a symlink. It leaves PATH and configuration unchanged, uses your current privileges, and leaves service shutdown and restart to you.

Envd downloads the matching release archive, verifies `SHA256SUMS`, checks the extracted binary's `--version`, then replaces the executable. Download and validation failures preserve the existing binary. If interrupted during replacement, inspect the executable with `--version` before retrying. A forcibly terminated standalone updater can leave a `.a13n-envd-update-*` scratch directory beside the executable; remove that specific directory only after confirming the updater has exited.

Hosts can install another exact version into a separate absolute directory with `--version ... --install-dir ...`, retaining the source installation. `--staging-dir` accepts an existing caller-owned scratch directory for cleanup after an interrupted child process exits. Harness UI uses these options for its version-selected cache. A `.a13n-envd-version` marker pins a managed destination; update it through Harness UI rather than changing the cache entry to another version.

For binaries without `update` support, use the standalone installer above once to install a release with this command. Stop the daemon before that replacement as well. On macOS, follow [Restore macOS permissions after an upgrade](computer-use.md#restore-macos-permissions-after-an-upgrade) only if Envd reports missing permissions; its permission-timeout error includes recovery guidance.

## Run the sandbox image

Use the standard [sandbox image](sandbox.md) for an agent development container with shell commands enabled. Envd runs as root; commands and file operations use the provisioned `1000:1000` account with passwordless sudo. Enable controlled egress explicitly when needed. The image guide covers local builds, stdio and outbound Host connections, persistence, and overrides.

## Next step

With the matching binary selected, try the [Local Envd Provider](index.md#try-local-envd) or [configure your own daemon](configuration.md). Before running untrusted work, choose the Host's outer boundary using [Execution boundaries](isolation.md).
