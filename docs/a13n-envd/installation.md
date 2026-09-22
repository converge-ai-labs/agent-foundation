# Install Envd

Choose a native daemon version that matches your EIP client. If you only use Harness UI, its Local EIP runtime already manages matching-binary acquisition; you do not need a second standalone installation.

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

For a published Local Envd installation, select the native `a13n-envd` release matching the installed `a13n-envd-client` version, not the independently versioned Harness or UI. Python RC metadata such as `0.0.5rc1` corresponds to native `0.0.5-rc.1`. Harness UI manages this selection and acquisition for Local EIP; standalone Hosts supply their executable explicitly. Do not combine an arbitrary registry binary with the source workspace.

## Install a published binary

The standalone installers download one native executable from the repository's GitHub Releases. They do not install the Python client, start a daemon, register a service, or change Harness UI's managed runtime. Select the exact version matching your client when integrating a Host. The scripts use public GitHub endpoints without credentials; private repositories or inaccessible releases return an error rather than prompting for authentication.

### Linux and macOS

Requires a POSIX shell, `curl`, `tar`, and `sha256sum` or `shasum`. Automatic stable-version selection also requires `jq`; an explicit `--version` does not.

Download the script, inspect it, then run it:

```bash
curl -fsSLo install-a13n-envd.sh \
  https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.sh
sh install-a13n-envd.sh --version 0.0.5 --install-dir "$HOME/.local/bin"
"$HOME/.local/bin/a13n-envd" --version
```

### Windows

Use Windows PowerShell 5.1 or PowerShell 7 on Windows:

```powershell
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/main/scripts/install-a13n-envd.ps1' -OutFile install-a13n-envd.ps1
# Inspect the downloaded script before running it.
& .\install-a13n-envd.ps1 --version 0.0.5 --install-dir "$env:LOCALAPPDATA\A13N\bin"
& "$env:LOCALAPPDATA\A13N\bin\a13n-envd.exe" --version
```

Run scripts according to your organization's PowerShell execution policy; the installer does not change that policy.

### Options and behavior

Both scripts accept the same options:

| Option                               | Environment default             | Behavior                                                                                                                                                                                                                 |
| ------------------------------------ | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--version X.Y.Z`                    | `A13N_ENVD_VERSION`             | Exact native version; canonical RCs such as `0.0.6-rc.1` require explicit selection. Omit both to select the first stable envd release in GitHub's newest-first release list, skipping other components and prereleases. |
| `--install-dir PATH`                 | `A13N_ENVD_INSTALL_DIR`         | Absolute destination directory. Defaults to `~/.local/bin` for POSIX users, `/usr/local/bin` for POSIX root, or `%LOCALAPPDATA%\A13N\bin` on Windows.                                                                    |
| `--add-to-path` / `--no-add-to-path` | `A13N_ENVD_ADD_TO_PATH=1` / `0` | Mutually exclusive. Default is no PATH modification. Explicit flags override environment values.                                                                                                                         |
| `--help`                             | —                               | Show usage without installing.                                                                                                                                                                                           |

PATH opt-in updates only the current user's configuration: Bash `.bashrc` (`.bash_profile` on macOS), Zsh `${ZDOTDIR:-$HOME}/.zshrc`, POSIX shell `.profile`, Fish `${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish`, or Windows user `Path`. Re-running does not append the same entry again. Open a new terminal afterward. Unsupported shells can use `--no-add-to-path` and configure PATH manually.

The installers support x86_64 and ARM64 on Linux, macOS, and Windows. They verify the selected archive against the release's `SHA256SUMS` **before extraction**, stage the executable on the destination filesystem, and atomically install or replace it. Download, checksum, archive, or replacement failures leave an existing executable unchanged. SHA256 verifies the archive against the manifest delivered by GitHub; it is not a separate signature or trust source.

Re-run the script with another exact version to replace the binary. This is an explicit install operation, not a self-updater. No release automation or Harness UI acquisition code invokes these scripts. Installing the binary does not create an outer account, container, or sandbox boundary.

## Run the sandbox image

For a ready-to-use Agent development container, use the [sandbox image](sandbox.md). It runs envd as root while commands and file operations use the provisioned `1000:1000` account, with passwordless sudo available. Shell commands are enabled and controlled egress is opt-in. The guide covers local builds, stdio and outbound Host connections, persistence and overrides.

## Next step

Use the [Local Envd Provider](index.md#recommended-harness-path), or [configure a standalone daemon](configuration.md) when you own the transport and lifecycle. Select the [Host security boundary](isolation.md) before running untrusted work.
