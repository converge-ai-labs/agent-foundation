---
title: Daemon configuration and transports
sidebarTitle: Configuration and transports
description: Configure a standalone Envd daemon and how it connects to a Host.
---

This is standalone Envd configuration, not Harness UI YAML or an `EnvironmentProviderSpec`. The Host owns deployment, account selection, any outer sandbox, credentials and the daemon lifetime. An adapter configuration selects a working directory, required methods and an optional Session egress policy whose secrets are references, not values. The Device launch configuration separately fixes execution, Sandbox and network mode. The [sandbox image](sandbox.md) supplies ready-to-use account, shell and sudo defaults without changing the standalone daemon defaults.

## Configuration layers

Startup precedence, from lowest to highest:

1. Built-in defaults.
2. Daemon JSON selected by `--config`.
3. `A13N_ENVD_CONFIG_JSON`, using the same strict schema as the file.
4. Scalar `A13N_ENVD_*` environment variables.
5. Explicit command-line arguments.

Objects merge recursively; arrays and scalar values replace earlier values. Each JSON layer must be valid even if a later layer overrides it. This lets a sandbox launcher configure Envd entirely through environment variables, including nested limits and shell profiles, without writing a configuration file:

```bash
export A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
export A13N_ENVD_DEVICE_ID=device-sandbox
export A13N_ENVD_FULL_CONTROL=true
export A13N_ENVD_CONFIG_JSON='{"default_working_directory":"/workspace","limits":{"max_file_bytes":104857600}}'
a13n-envd
```

Scalar shortcuts include `A13N_ENVD_ALLOW_SUDO`, `A13N_ENVD_EXECUTION_UID`, `A13N_ENVD_EXECUTION_GID`, `A13N_ENVD_EGRESS_MODE`, `A13N_ENVD_FULL_CONTROL`, `A13N_ENVD_COMPUTER_USE`, `A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS`, `A13N_ENVD_DIRECTORY_DISCOVERY`, `A13N_ENVD_DEVICE_ID`, `A13N_ENVD_NAME`, `A13N_ENVD_DESCRIPTION`, `A13N_ENVD_DEFAULT_WORKING_DIRECTORY`, `A13N_ENVD_IDLE_TIMEOUT_MS`, and `A13N_ENVD_DISCONNECT_GRACE_MS`. Booleans accept `true`, `false`, `1`, or `0`.

Device initialization negotiates the protocol and verifies the Device identity. EIP Sessions select a fixed cwd and own operations, processes, output and transfers; they cannot change trusted startup identity or sudo policy.

## Connect to Harness UI

To connect to Harness UI, start one outbound connection:

```console
a13n-envd connect https://host.example.com --host work
```

1. Open the approval address that Envd prints.
2. Sign in to the Host.
3. Approve the matching verification code.

Keep Envd running. On later starts, `a13n-envd connect work` reuses its saved identity, credential and approved connection. The credential is generated on the Device and never needs to be copied into the browser. Reconnection does not require another approval. Service instead connects to an HTTP daemon you register; see [Connect to the Service](../environments/remote-envd.md#connect-to-the-service).

The Host URL must be reachable from this computer. HTTPS is required except for loopback HTTP, such as `http://127.0.0.1:8765`. Add `--ca-file /path/to/ca.pem` for a private CA; TLS verification is never disabled. `--name` sets the approval display name, and `--default-working-directory` selects an existing native directory. `--config` supplies ordinary daemon JSON, including resource limits and command settings.

### Saved instances and Hosts

State defaults to `~/.a13n-envd` on Unix and `%LOCALAPPDATA%/a13n-envd` on Windows (falling back to the user profile). Override it with `--state-dir` or `A13N_ENVD_STATE_DIR`. Each instance stores its identity at `instances/<instance>/device-id`; named Host configuration, credential and private runtime live under `instances/<instance>/hosts/<host>/`. The default instance name is `default`. Without `--host`, a stable name is derived from the URL. Keep this state across restarts and protect it as an account credential.

Each running Envd process connects to **one Host**. To use two Hosts on the same physical computer, run two independent processes:

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

Each process has its own registration and Sessions. Restart with the same `--instance` and Host alias. `connect` stays in the foreground; it does not install a system service or modify OS startup settings. If you use a service manager, preserve the launching account, state directory and intended command environment.

Authentication rejection stops the daemon without replacing its credential or requesting new trust. Check whether the registration was revoked or forgotten, whether this is the intended Host, and whether the correct state directory is mounted. Do not repeatedly delete credentials to hide an authorization failure. A genuinely new installation requires a new instance and explicit approval.

## Minimal standalone configuration

The default carrier is stdio. Supply an absolute private runtime directory:

```bash
export A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
export A13N_ENVD_DEVICE_ID=device-my-machine
a13n-envd --config /absolute/path/to/a13n-envd.json
```

The Host creates and protects the runtime parent. Envd creates an unpredictable generation-private child and takes an exclusive runtime lock. Stdin and stdout are reserved for framed EIP traffic; diagnostics use stderr.

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "directory_discovery": true,
  "limits": {
    "max_file_bytes": 104857600
  }
}
```

A Session can open only in an existing directory; a missing default fails Session opening, not daemon startup. If `default_working_directory` is omitted, the daemon captures its startup cwd. The working directory is **not an access root**: file paths address the whole filesystem available to the execution account, the Sandbox grants and the outer sandbox. Directory discovery is a bounded, one-level read that works before any Session exists.

Explicit Device IDs can come from `--device-id`, JSON `device_id`, or `A13N_ENVD_DEVICE_ID`. Without an explicit ID, an installation state directory retains the generated identity across restart. `--default-working-directory`, `--name` and `--description` override JSON metadata. Set directory discovery through JSON or `A13N_ENVD_DIRECTORY_DISCOVERY`. EIP paths use `/C:/...` and `/UNC/server/share/...` on Windows; daemon bootstrap paths use native OS spelling.

## Enable commands

For native shell execution with the configured execution account's authority, use:

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

Or set `"full_control": true` in daemon JSON. `A13N_ENVD_FULL_CONTROL` overrides the JSON value; `false` or `0` disables it. Full Control creates the default native shell profile automatically: `/bin/sh` on Unix, or PowerShell on Windows. Commands inherit the launching process's environment and original `PATH` order, excluding daemon bootstrap variables (`A13N_ENVD_*` and `EIP_*`). Command-level environment changes do not modify the daemon or later commands. Shell aliases and unexported variables are not inherited.

Envd Full Control (`full_control`) is not a sandbox. It is independent of Harness UI's local Full Control or Sandbox mode. Do not combine it with manual executable roots or shell profiles. To configure command access explicitly instead, supply trusted executable search roots and optionally fixed shell profiles:

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "trusted_executable_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
  "shell_profiles": [
    {
      "profile_id": "sh",
      "display_name": "POSIX shell",
      "native_executable": "/bin/sh",
      "fixed_arguments": ["-c"],
      "safe_base_environment": {},
      "executable_search_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
      "max_script_bytes": 1048576,
      "allow_login_mode": false
    }
  ]
}
```

No command methods are advertised when Full Control is disabled and both executable roots and shell profiles are empty. Executable roots and profiles are trusted launch configuration, not a Session sandbox. The Host supplies the outer security boundary; optional [controlled Session egress](egress.md) adds per-Session destination restrictions and credential injection. See [execution boundaries and troubleshooting](isolation.md).

Child environments are built from the daemon's supported inherited values plus explicit command inputs. Daemon control variables are not command configuration. Inspect advertised execution features instead of assuming platform support for signals, limits or executable bits.

## Native identity and sudo

> [!IMPORTANT]
> **Native sudo is allowed by default.** With disabled Sandbox, Envd keeps the outer sandbox's original system tree writable. Installing packages or changing system files through authorized sudo changes that tree and persists across Sessions; no disposable copy of the payload root filesystem is introduced.

On Linux, **omitting execution UID/GID preserves the launching process's identity, including root**. Envd does not assume `1000:1000`, scan for a likely user, create an account, or require identity configuration merely because the launcher is root. To select a different provisioned account, set the paired `execution.uid`/`execution.gid`, `A13N_ENVD_EXECUTION_UID`/`A13N_ENVD_EXECUTION_GID`, or `--execution-uid`/`--execution-gid`. Root (`0:0`) is also a valid explicit identity. The Provider or deployment owns account provisioning, home and working-directory permissions. For example, a root launcher can select its provisioned sandbox account:

```bash
# Replace sandbox with the account provisioned by your image or deployment.
export A13N_ENVD_EXECUTION_UID="$(id -u sandbox)"
export A13N_ENVD_EXECUTION_GID="$(id -g sandbox)"
a13n-envd
```

Commands, Session file RPCs (including reads, writes and transfers), working-directory access and Device directory discovery all use this execution identity and its native permissions. File RPCs never implicitly sudo; insufficient access returns a permission error. Explicit sudo affects only the command that invokes it and its descendants, not later file RPCs. Daemon credential loading and broker management remain separate from execution identity.

Switching accounts initializes native supplementary groups and account environment defaults. Retaining the launcher's identity preserves native capability inheritance for both root and non-root users; Envd does not unconditionally clear command capabilities. Only the supervisor signaling capability retained by Envd during a root-to-user switch is removed before executing commands. The outer platform and any explicitly enabled controlled-egress boundary still apply. An unprivileged launcher cannot choose a different identity.

`allow_sudo` permits native privilege gains; it does not grant authorization. Install sudo and configure sudoers through your image or launcher. Envd does not add passwordless sudo rules, bypass authentication, or override an outer `no_new_privs` setting or `nosuid` mount.

Disable privilege gains in any of these ways:

```bash
# Environment variable
A13N_ENVD_ALLOW_SUDO=false a13n-envd

# CLI, including the connect command
a13n-envd --allow-sudo false
a13n-envd connect work --allow-sudo false

# Same nested setting without a file
A13N_ENVD_CONFIG_JSON='{"execution":{"allow_sudo":false}}' a13n-envd
```

Or use a JSON file:

```json
{
  "execution": {"allow_sudo": false}
}
```

The normal precedence applies: for example, `--allow-sudo true` overrides an environment value of `false`. Disabling uses Linux `no_new_privs` for the worker and descendants, so direct sudo, setuid binaries and file capabilities cannot gain additional privilege. This does not demote an already-root execution identity or revoke its existing authority. A command's own environment cannot turn it back on. Platforms without this implementation reject `false` instead of ignoring it. UID/GID configuration is Linux-only; other platforms retain their native launching identity.

## Carrier profiles

| Profile             | Bootstrap                                                                        | Use                                         |
| ------------------- | -------------------------------------------------------------------------------- | ------------------------------------------- |
| `stdio`             | Parent-supplied process pipes                                                    | Host-owned local daemon                     |
| `http`              | Bind address, protected credential file, TLS or explicit trusted plaintext scope | Host-dialed Device                          |
| `reverse_websocket` | Outbound URL and protected credential file                                       | Device dials an authenticated Host listener |

Select the profile with `A13N_ENVD_TRANSPORT`.

HTTP requires `A13N_ENVD_HTTP_BIND`, `A13N_ENVD_HTTP_CREDENTIAL_FILE`, and either paired TLS certificate/key files or `A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback|provider_private_link`. It exposes authenticated `/eip/control` and `/eip/transfer` routes. Session selection is explicit; neither a TCP connection nor an HTTP pool owns a Session.

Reverse WebSocket requires `A13N_ENVD_REVERSE_WS_URL` and `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`. `A13N_ENVD_REVERSE_WS_CA_FILE` adds deployment trust for `wss`. The daemon does not expose an inbound WebSocket listener.

Credentials belong in protected files, not argv, endpoint URLs, descriptors, logs or portable Environment state. A credential authorizes Device access; it does not isolate Sessions from each other.

## Lifecycle and ownership

One daemon generation serves a Device with multiple independent Sessions. Initializing a carrier opens no Session. `session.open` captures a working directory and starts one resource scope; `session.close` closes only that scope. Session-local keepalive renews only its owner. Device discovery does not keep abandoned Sessions alive.

A lost framed carrier detaches its Sessions for bounded disconnect grace. An existing owner may explicitly attach the same Session in the same generation. Attachment never replays commands or resumes transfers. Expired Sessions require fresh scopes and old resource references remain invalid.

The Host closes its Device connections during shutdown and terminates a daemon only when it owns that daemon's lifecycle. Session close does not delete files in the working directory. Native processes and private output/staging storage are reclaimed under bounded cleanup; failure is reported rather than treated as successful reclamation.

## Check before admitting work

```bash
a13n-envd --version
```

Match the exact daemon and Python client release. `A13N_ENVD_EXECUTABLE` belongs to Python Host executable selection; remove it from the daemon child environment. Unknown `A13N_ENVD_*` variables and unknown JSON fields are rejected.

Device initialization verifies identity and protocol. Session readiness checks the selected scope. There is no `isolation probe` or per-command isolation mode: test the Host's outer boundary through that deployment's own launcher and checks.

## Standalone field reference

| Root field                                   | Default or meaning                                                                                                                         |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `device_id`                                  | Explicit stable identity, otherwise installation identity                                                                                  |
| `installation_state_directory`               | Persistent identity storage when no explicit Device ID is supplied                                                                         |
| `name`, `description`                        | Optional display metadata                                                                                                                  |
| `default_working_directory`                  | Startup cwd if unset                                                                                                                       |
| `directory_discovery`                        | `true`                                                                                                                                     |
| `idle_timeout_ms`                            | Session idle lifetime                                                                                                                      |
| `disconnect_grace_ms`                        | Detached Session attachment grace                                                                                                          |
| `execution.uid`, `execution.gid`             | Optional paired Linux native IDs; omitted retains the launcher, including root                                                             |
| `execution.allow_sudo`                       | `true`; native sudoers still controls authorization                                                                                        |
| `sandbox`                                    | `{"mode":"disabled"}`; or `restricted` with directory `grants`                                                                             |
| `egress`                                     | `{"mode":"inherit"}`; or `deny` / `controlled`                                                                                             |
| `full_control`                               | `false`; automatic native shell and inherited command environment when enabled                                                             |
| `computer_use`                               | `false`; opt-in macOS/X11/Windows screenshot/input methods with disabled Sandbox and inherited egress; see [computer use](computer-use.md) |
| `computer_use_permission_timeout_ms`         | `120000`; positive startup deadline for macOS permissions or X11/Windows desktop readiness before any EIP transport starts                 |
| `trusted_executable_roots`, `shell_profiles` | Empty; command methods disabled unless Full Control is enabled                                                                             |
| `limits`                                     | Device aggregates and per-Session limits                                                                                                   |

Important default limits include 128 Sessions, 256 Device concurrent operations, 128 concurrent operations per Session, 4 GiB Device spool capacity, 1 GiB Session spool capacity, 256 MiB output per stream and 2 MiB previews. Configured Session capacities cannot exceed Device aggregates. Output preview cannot exceed stream capacity; spool must reserve both streams.

### Shell-profile fields

Required fields are `profile_id`, `display_name`, `native_executable`, `executable_search_roots` and positive `max_script_bytes`. Executables and search roots are existing absolute native paths. `fixed_arguments` defaults to `[]`, `safe_base_environment` to `{}` and `allow_login_mode` to `false`.

## Controlled Session egress

Set `"egress": {"mode": "controlled"}` in daemon JSON, `A13N_ENVD_EGRESS_MODE=controlled`, or `--egress-mode controlled` to require Linux controlled Sessions. Every Session must provide explicit tagged destinations; inherit and deny reject Session egress policy. Secret values are submitted through EIP, never this file. See [Session egress](egress.md) for prerequisites, creation, live updates and capacity behavior.
