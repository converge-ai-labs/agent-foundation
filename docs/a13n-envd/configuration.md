# Daemon configuration and transports

This is standalone Envd configuration, not Harness UI YAML or an `EnvironmentProviderSpec`. The Host owns deployment, account selection, any outer sandbox, credentials and the daemon lifetime. An adapter configuration selects only a working directory and required methods.

## Configuration layers

| Layer                    | Holds                                                                                   |
| ------------------------ | --------------------------------------------------------------------------------------- |
| Daemon JSON (`--config`) | Device metadata, default cwd, directory discovery, command profiles and resource limits |
| Bootstrap environment    | Device identity, private runtime, carrier and credential-file paths                     |
| Device connection        | Negotiated protocol and shared carrier                                                  |
| EIP Session              | Fixed cwd and independent operations, processes, output, transfers and evidence         |

## Connect to a Host

For a Harness UI or a13n Service Host with device registration, start one outbound connection:

```console
a13n-envd connect https://host.example.com --host work
```

Open the approval address printed by envd, sign in to the Host, and approve the matching verification code. Keep envd running. On later starts, `a13n-envd connect work` reuses its saved identity, credential and approved connection. The credential is generated on the Device and never needs to be copied into the browser. Reconnection does not require a fresh ticket or another approval.

The Host URL must be reachable from this computer. HTTPS is required except for loopback HTTP, such as `http://127.0.0.1:8765`. Add `--ca-file /path/to/ca.pem` for a private CA; TLS verification is never disabled. `--name` sets the approval display name, and `--default-working-directory` selects an existing native directory. `--config` supplies ordinary daemon JSON, including resource limits and command settings.

### Saved instances and Hosts

State defaults to `~/.a13n-envd` on Unix and `%LOCALAPPDATA%/a13n-envd` on Windows (falling back to the user profile). Override it with `--state-dir` or `A13N_ENVD_STATE_DIR`. Each instance stores its identity at `instances/<instance>/device-id`; named Host configuration, credential and private runtime live under `instances/<instance>/hosts/<host>/`. The default instance name is `default`. Without `--host`, a stable name is derived from the URL. Keep this state across restarts and protect it as an account credential.

Each running envd connects to **one Host**. To use two Hosts on the same physical computer, run two independent processes:

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

The working directory must exist. If omitted, the daemon captures its startup cwd. It is **not an access root**: file paths address the whole filesystem available to the daemon account and outer sandbox. Directory discovery is a bounded, one-level read that works before any Session exists.

Explicit Device IDs can come from `--device-id`, JSON `device_id`, or `A13N_ENVD_DEVICE_ID`. Without an explicit ID, an installation state directory retains the generated identity across restart. `--default-working-directory`, `--name` and `--description` override JSON metadata. Set directory discovery through JSON or `A13N_ENVD_DIRECTORY_DISCOVERY`. EIP paths use `/C:/...` and `/UNC/server/share/...` on Windows; daemon bootstrap paths use native OS spelling.

## Enable commands

For native shell execution with the launching account's authority, use:

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

Or set `"full_control": true` in daemon JSON. An explicit JSON value takes precedence over `A13N_ENVD_FULL_CONTROL`; `false` or environment value `0` disables it. Full Control creates the default native shell profile automatically: `/bin/sh` on Unix, or PowerShell on Windows. Commands inherit the launching process's environment and original `PATH` order, excluding daemon bootstrap variables (`A13N_ENVD_*` and `EIP_*`). Command-level environment changes do not modify the daemon or later commands. Shell aliases and unexported variables are not inherited.

Full Control is not a sandbox and is independent of the Host's local execution profile. Do not combine it with manual executable roots or shell profiles. To configure command access explicitly instead, supply trusted executable search roots and optionally fixed shell profiles:

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

No command methods are advertised when Full Control is disabled and both executable roots and shell profiles are empty. Executable roots and profiles are trusted launch configuration, not a Session sandbox. Filesystem, account and network restrictions must surround the entire daemon. See [outer security and troubleshooting](isolation.md).

Child environments are built from the daemon's supported inherited values plus explicit command inputs. Daemon control variables are not command configuration. Inspect advertised execution features instead of assuming platform support for signals, limits or executable bits.

## Carrier profiles

| Profile             | Bootstrap                                                                        | Use                                         |
| ------------------- | -------------------------------------------------------------------------------- | ------------------------------------------- |
| `stdio`             | Parent-supplied process pipes                                                    | Host-owned local daemon                     |
| `http`              | Bind address, protected credential file, TLS or explicit trusted plaintext scope | Host-dialed Device                          |
| `reverse_websocket` | Outbound URL and protected credential file                                       | Device dials an authenticated Host listener |

Select the profile with `A13N_ENVD_TRANSPORT`.

HTTP requires `A13N_ENVD_HTTP_BIND`, `A13N_ENVD_HTTP_CREDENTIAL_FILE`, and either paired TLS certificate/key files or `A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback|provider_private_link`. It exposes authenticated `/eip/control` and `/eip/transfer` routes. Session selection is explicit; neither a TCP connection nor an HTTP pool owns a Session.

Reverse WebSocket requires `A13N_ENVD_REVERSE_WS_URL` and `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`. `A13N_ENVD_REVERSE_WS_CA_FILE` adds deployment trust for `wss`. The daemon does not expose an inbound WebSocket listener.

Credentials belong in protected files, not argv, endpoint URLs, descriptors, logs or portable Environment state. A credential authorizes Device access, not a tenant-isolated Session.

## Lifecycle and ownership

One daemon generation serves a Device with multiple independent Sessions. Initializing a carrier opens no Session. `session.open` captures a working directory and starts one resource scope; `session.close` closes only that scope. Session-local keepalive renews only its owner. Device discovery does not keep abandoned Sessions alive.

A lost framed carrier detaches its Sessions for bounded disconnect grace. An existing owner may explicitly attach the same Session in the same generation. Attachment never replays commands or resumes transfers. Expired Sessions require fresh scopes and old resource references remain invalid.

The Host closes its Device connections during shutdown and terminates a daemon only when it owns that daemon's lifecycle. Workspace files are not deleted by Session close. Native processes and private output/staging storage are reclaimed under bounded cleanup; failure is reported rather than treated as successful reclamation.

## Check before admitting work

```bash
a13n-envd --version
```

Match the exact daemon and Python client release. `A13N_ENVD_EXECUTABLE` belongs to Python Host executable selection; remove it from the daemon child environment. Unknown `A13N_ENVD_*` variables and unknown JSON fields are rejected.

Device initialization verifies identity and protocol. Session readiness checks the selected scope. There is no `isolation probe` or per-command isolation mode: test the Host's outer boundary through that deployment's own launcher and checks.

## Standalone field reference

| Root field                                   | Default or meaning                                                             |
| -------------------------------------------- | ------------------------------------------------------------------------------ |
| `device_id`                                  | Explicit stable identity, otherwise installation identity                      |
| `installation_state_directory`               | Persistent identity storage when no explicit Device ID is supplied             |
| `name`, `description`                        | Optional display metadata                                                      |
| `default_working_directory`                  | Startup cwd if unset                                                           |
| `directory_discovery`                        | `true`                                                                         |
| `idle_timeout_ms`                            | Session idle lifetime                                                          |
| `disconnect_grace_ms`                        | Detached Session attachment grace                                              |
| `full_control`                               | `false`; automatic native shell and inherited command environment when enabled |
| `trusted_executable_roots`, `shell_profiles` | Empty; command methods disabled unless Full Control is enabled                 |
| `limits`                                     | Device aggregates and per-Session limits                                       |

Important default limits include 128 Sessions, 256 Device concurrent operations, 128 concurrent operations per Session, 4 GiB Device spool capacity, 1 GiB Session spool capacity, 256 MiB output per stream and 2 MiB previews. Configured Session capacities cannot exceed Device aggregates. Output preview cannot exceed stream capacity; spool must reserve both streams.

### Shell-profile fields

Required fields are `profile_id`, `display_name`, `native_executable`, `executable_search_roots` and positive `max_script_bytes`. Executables and search roots are existing absolute native paths. `fixed_arguments` defaults to `[]`, `safe_base_environment` to `{}` and `allow_login_mode` to `false`.
