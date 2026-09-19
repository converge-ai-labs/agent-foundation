# Daemon configuration and transports

This is standalone Envd configuration, not Harness UI YAML or an `EnvironmentProviderSpec`. The Host owns deployment, account selection, any outer sandbox, credentials and the daemon lifetime. An adapter configuration selects only a working directory and required methods.

## Configuration layers

| Layer                    | Holds                                                                                   |
| ------------------------ | --------------------------------------------------------------------------------------- |
| Daemon JSON (`--config`) | Device metadata, default cwd, directory discovery, command profiles and resource limits |
| Bootstrap environment    | Device identity, private runtime, carrier and credential-file paths                     |
| Device connection        | Negotiated protocol and shared carrier                                                  |
| EIP Session              | Fixed cwd and independent operations, processes, output, transfers and evidence         |

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

Configure trusted executable search roots and optionally fixed shell profiles:

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

No command methods are advertised when both executable roots and shell profiles are empty. Executable roots and profiles are trusted launch configuration, not a Session sandbox. Filesystem, account and network restrictions must surround the entire daemon. See [outer security and troubleshooting](isolation.md).

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

| Root field                                   | Default or meaning                                                 |
| -------------------------------------------- | ------------------------------------------------------------------ |
| `device_id`                                  | Explicit stable identity, otherwise installation identity          |
| `installation_state_directory`               | Persistent identity storage when no explicit Device ID is supplied |
| `name`, `description`                        | Optional display metadata                                          |
| `default_working_directory`                  | Startup cwd if unset                                               |
| `directory_discovery`                        | `true`                                                             |
| `idle_timeout_ms`                            | Session idle lifetime                                              |
| `disconnect_grace_ms`                        | Detached Session attachment grace                                  |
| `trusted_executable_roots`, `shell_profiles` | Empty; command methods disabled                                    |
| `limits`                                     | Device aggregates and per-Session limits                           |

Important default limits include 128 Sessions, 256 Device concurrent operations, 128 concurrent operations per Session, 4 GiB Device spool capacity, 1 GiB Session spool capacity, 256 MiB output per stream and 2 MiB previews. Configured Session capacities cannot exceed Device aggregates. Output preview cannot exceed stream capacity; spool must reserve both streams.

### Shell-profile fields

Required fields are `profile_id`, `display_name`, `native_executable`, `executable_search_roots` and positive `max_script_bytes`. Executables and search roots are existing absolute native paths. `fixed_arguments` defaults to `[]`, `safe_base_environment` to `{}` and `allow_login_mode` to `false`.
