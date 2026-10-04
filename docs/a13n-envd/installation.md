---
title: Install Envd
sidebarTitle: Installation
description: Build or download an Envd binary that matches your EIP client, or run the sandbox image.
---

Choose a native daemon version that matches your EIP client. If you use only Harness UI's local Sandbox mode, Harness UI acquires the matching binary itself; you do not need a standalone installation on that computer. To connect another computer to Harness UI as a Device, install Envd on that computer.

## Build the matching binary

These pages track `main`. Build `a13n-envd` from the same checkout as the Python workspace so the daemon and client pass Local Envd's exact release check:

```bash
cargo build --locked --package a13n-envd
```

Then select that binary for Local Envd:

```bash
export A13N_ENVD_EXECUTABLE="$PWD/target/debug/a13n-envd"
```

The Python `a13n-envd-client` package does not discover, install, or launch the binary. A Provider or Host supplies process lifecycle and transport policy.

For a published Local Envd installation, select the native `a13n-envd` release matching the installed `a13n-envd-client` version, not the independently versioned Harness or Harness UI. Python RC metadata such as `1.2.3rc1` corresponds to native `1.2.3-rc.1`. Harness UI manages this selection and acquisition for its Sandbox mode; standalone Hosts supply their executable explicitly. Do not combine an arbitrary published binary with a client built from the source workspace.

## Install a published binary

The standalone installers download the native executable from GitHub Releases. Install the Python client separately and select the matching native version.

### Linux and macOS

Requires a POSIX shell, `curl`, `tar`, and `sha256sum` or `shasum`. Automatic stable-version selection also requires `jq`; an explicit `--version` does not.

Download the script, inspect it, then run it. With a **published** client installed in this Python environment, obtain its matching native version (including an RC when applicable):

```bash
curl -fsSLo install-a13n-envd.sh \
  https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.sh
# Inspect install-a13n-envd.sh before running it.
ENVD_VERSION="$(python -c 'from importlib.metadata import version; import re; print(re.sub(r"rc([0-9]+)$", r"-rc.\1", version("a13n-envd-client")))')"
sh install-a13n-envd.sh --version "$ENVD_VERSION" --install-dir "$HOME/.local/bin"
"$HOME/.local/bin/a13n-envd" --version
```

### Windows

Use Windows PowerShell 5.1 or PowerShell 7 on Windows. With a **published** client installed in the selected Python environment:

```powershell
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.ps1' -OutFile install-a13n-envd.ps1
# Inspect the downloaded script before running it.
$envdVersion = python -c "from importlib.metadata import version; import re; print(re.sub(r'rc([0-9]+)$', r'-rc.\1', version('a13n-envd-client')))"
& .\install-a13n-envd.ps1 --version $envdVersion --install-dir "$env:LOCALAPPDATA\A13N\bin"
& "$env:LOCALAPPDATA\A13N\bin\a13n-envd.exe" --version
```

Run scripts according to your organization's PowerShell execution policy; the installer does not change that policy.

### Options and behavior

Both scripts accept the same options:

| Option                               | Environment default             | Behavior                                                                                                                                                                                                                 |
| ------------------------------------ | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--version X.Y.Z`                    | `A13N_ENVD_VERSION`             | Exact native version; canonical RCs such as `0.0.6-rc.1` require explicit selection. Omit both to select the first stable Envd release in GitHub's newest-first release list, skipping other components and prereleases. |
| `--install-dir PATH`                 | `A13N_ENVD_INSTALL_DIR`         | Absolute destination directory. Defaults to `~/.local/bin` for POSIX users, `/usr/local/bin` for POSIX root, or `%LOCALAPPDATA%\A13N\bin` on Windows.                                                                    |
| `--add-to-path` / `--no-add-to-path` | `A13N_ENVD_ADD_TO_PATH=1` / `0` | Mutually exclusive. Default is no PATH modification. Explicit flags override environment values.                                                                                                                         |
| `--help`                             | —                               | Show usage without installing.                                                                                                                                                                                           |

PATH opt-in updates only the current user's configuration: Bash `.bashrc` (`.bash_profile` on macOS), Zsh `${ZDOTDIR:-$HOME}/.zshrc`, POSIX shell `.profile`, Fish `${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish`, or Windows user `Path`. Re-running does not append the same entry again. Open a new terminal afterward. Unsupported shells can use `--no-add-to-path` and configure PATH manually.

The installers support x86_64 and ARM64 on Linux, macOS, and Windows. They verify the selected archive against the release's `SHA256SUMS` **before extraction**, stage the executable on the destination filesystem, and atomically install or replace it. Download, checksum, archive, or replacement failures leave an existing executable unchanged. SHA256 verifies the archive against the manifest delivered by GitHub; it is not a separate signature or trust source.

Re-run the script with another exact version to replace the binary.

## Run the sandbox image

For a ready-to-use agent development container, use the [sandbox image](sandbox.md). It runs Envd as root while commands and file operations use the provisioned `1000:1000` account, with passwordless sudo available. Shell commands are enabled and controlled egress is opt-in. The guide covers local builds, stdio and outbound Host connections, persistence and overrides.

## Next step

Use the [Local Envd Provider](index.md#try-local-envd), or [configure a standalone daemon](configuration.md) when you own the transport and lifecycle. Read [execution boundaries](isolation.md) and select the Host's outer boundary before running untrusted work.
