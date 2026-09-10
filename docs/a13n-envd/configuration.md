# Daemon configuration and transports

This is the standalone Envd configuration, not Harness UI YAML or an `EnvironmentProviderSpec`. Use it when operating a daemon directly. For normal Harness integration, let the Local Envd Provider construct its trusted bootstrap.

## Configuration layers

| Layer                    | Holds                                                                 | Does not hold                                      |
| ------------------------ | --------------------------------------------------------------------- | -------------------------------------------------- |
| Daemon JSON (`--config`) | Mounts, operation permissions, execution policy, shell profiles       | Agent definitions or model settings                |
| Bootstrap environment    | Environment identity, private runtime, carrier, credential-file paths | Credential bytes or a product-user session         |
| EIP initialized session  | Negotiated methods and current operation correlation                  | Multi-tenant partitions or durable Agent execution |

## Minimal standalone configuration

The default carrier is stdio. A standalone process needs a stable Environment identity and an absolute private runtime parent:

```bash
export A13N_ENVD_ENVIRONMENT_ID=provider-owned-environment-id
export A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
a13n-envd --config /absolute/path/to/a13n-envd.json
```

The Provider creates and protects the runtime parent before launch. `a13n-envd` creates one unpredictable generation-private child. In stdio mode, stdin and stdout are reserved for framed EIP traffic; diagnostics use stderr.

A minimal workspace configuration is:

```json
{
  "root_mount_id": "workspace",
  "execution": {
    "isolation": "required",
    "network": "deny",
    "extra_read_only_paths": []
  },
  "mounts": [
    {
      "mount_id": "workspace",
      "native_root": "/absolute/path/to/workspace",
      "writable": true,
      "allow_command_execution": false,
      "max_file_bytes": 104857600
    }
  ]
}
```

Native mount roots must already exist. With one mount, `root_mount_id` is inferred; with multiple mounts, configure it explicitly. Roots must not overlap or alias.

**Omitting `allowed_operations` or setting it to `[]` selects the default operation set, not deny-all.** Only a non-empty list replaces the defaults. Read-only mounts remove write operations even if listed. For a file-only mount, set `allow_command_execution: false` explicitly; its standalone default is `true`.

## Enable commands

A command-enabled mount needs `command_cwd`. Executables come only from fixed trusted search roots or an explicitly eligible mounted executable source. Optional shell profiles are trusted configuration:

```json
{
  "mounts": [
    {
      "mount_id": "workspace",
      "native_root": "/absolute/path/to/workspace",
      "writable": false,
      "allow_command_execution": true,
      "max_file_bytes": 104857600,
      "allowed_operations": [
        "stat",
        "read_text",
        "open_reader",
        "list",
        "command_cwd",
        "executable_source"
      ]
    }
  ],
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

Child environments are rebuilt from a finite compatibility allowlist. `PATH`, `HOME`, and temporary-directory values are replaced; daemon control state, `A13N_ENVD_*`, ambient credentials, and dynamic-loader variables are removed.

## Carrier profiles

| Profile             | Required bootstrap                                                                          | Intended use                                                 |
| ------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `stdio`             | Parent-supplied process pipes                                                               | Local Provider-owned daemon                                  |
| `http`              | Bind address, protected credential file, and native TLS or explicit trusted plaintext scope | Host-dialed dedicated EIP listener                           |
| `reverse_websocket` | Outbound URL and protected credential file                                                  | Provider accepts an authenticated outbound daemon connection |

Select a profile with `A13N_ENVD_TRANSPORT`.

HTTP requires `A13N_ENVD_HTTP_BIND`, `A13N_ENVD_HTTP_CREDENTIAL_FILE`, and either paired TLS certificate/key files or `A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback|provider_private_link`. It exposes only authenticated `/eip/control` and `/eip/transfer` routes.

Reverse WebSocket requires `A13N_ENVD_REVERSE_WS_URL` and `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`. An optional CA file adds deployment trust for `wss`. The daemon dials outward; it does not expose an inbound WebSocket listener.

Credential files contain short-lived Provider-owned bearer tokens. Tokens do not belong in argv, ordinary environment variables, endpoint URLs, descriptors, logs, traces, or EIP payloads.

## Lifecycle and ownership

One daemon is one authority-bearing generation for one Environment identity. It admits at most one active initialized EIP session. Another user, mutually untrusted workload, or concurrent independent session needs another daemon, private runtime, and Provider adapter.

The daemon does not own Harness runs or durable workflow lifecycle. It also exposes no generic HTTP server, browser endpoint, health endpoint, readiness endpoint, or inbound WebSocket. Providers establish readiness through EIP initialization and readiness while observing process or carrier failure.

## Check before admitting work

```bash
a13n-envd --version
env -u A13N_ENVD_EXECUTABLE a13n-envd isolation probe \
  --config /absolute/path/to/a13n-envd.json --json
```

Use the ordinary daemon account and production-equivalent execution settings. The probe validates the execution posture and can start bounded local subprocesses and create temporary files; it is not a complete mount/runtime/carrier dry-run. `--json` is required. The daemon has no general `validate` subcommand.

`A13N_ENVD_EXECUTABLE` belongs to Python Host executable selection, not daemon bootstrap. Remove it when launching the daemon directly; unknown `A13N_ENVD_*` variables are rejected. The `env -u` form above is POSIX; on PowerShell, remove that variable from the child launch environment instead.

Inspect exact available methods after EIP initialization; a command-disabled mount must not be assumed to expose shell execution. See [isolation and troubleshooting](isolation.md) for platform prerequisites.

## Standalone field reference

These are **daemon JSON defaults**, not the defaults of Python's Local Envd Provider. The latter supplies its own bounded configuration. Unknown JSON fields are rejected; the config path must be an absolute regular file, not a symlink.

| Root field                           | Default / requirement                                                   |
| ------------------------------------ | ----------------------------------------------------------------------- |
| `root_mount_id`                      | Unset; inferred for one mount, required for multiple mounts             |
| `mounts`                             | `[]`                                                                    |
| `trusted_executable_roots`           | `[]`; absolute existing directories                                     |
| `shell_profiles`                     | `[]`                                                                    |
| `execution.isolation`                | `required`; `disabled` requires a deliberate outer containment boundary |
| `execution.network`                  | `host`; set `deny` explicitly for required network isolation            |
| `execution.extra_read_only_paths`    | `[]`; absolute existing directories                                     |
| `limits.max_output_preview_bytes`    | `2097152` (2 MiB)                                                       |
| `limits.max_output_bytes_per_stream` | `268435456` (256 MiB)                                                   |
| `limits.max_spool_bytes`             | `1073741824` (1 GiB)                                                    |

All three output limits must be positive; preview cannot exceed a single-stream limit and spool must be at least twice the single-stream limit. Other internal daemon limits are not accepted JSON fields.

### Mount fields

| Field                     | Default / requirement                                                            |
| ------------------------- | -------------------------------------------------------------------------------- |
| `mount_id`                | Required unique 1–128 character ASCII identifier: letters, digits, `.`, `-`, `_` |
| `native_root`             | Required absolute existing directory                                             |
| `writable`                | Required boolean                                                                 |
| `max_file_bytes`          | Required positive integer                                                        |
| `allow_command_execution` | `true`                                                                           |
| `allowed_operations`      | `[]`, meaning default operations rather than none                                |

The default set includes reads, metadata, listing, find/search, `command_cwd`, and `executable_source`. Writable mounts additionally permit file mutations. Command use still requires a command-enabled mount, configured executable policy, and a usable isolation posture.

### Shell-profile fields

Required fields are `profile_id`, `display_name`, `native_executable`, `executable_search_roots`, and positive `max_script_bytes`. The executable must be an absolute existing regular file; search roots must contain at least one directory.

`fixed_arguments` defaults to `[]`, `safe_base_environment` to `{}`, and `allow_login_mode` to `false`. Profiles are trusted launch configuration, not model-supplied shell names. No command policy is created when both the top-level executable roots and shell profiles are empty.

### Execution environment overrides

| Variable                                             | Meaning                                               |
| ---------------------------------------------------- | ----------------------------------------------------- |
| `A13N_ENVD_EXECUTION_ISOLATION`                      | `required` or `disabled`; overrides JSON              |
| `A13N_ENVD_EXECUTION_NETWORK`                        | `host` or `deny`; overrides JSON                      |
| `A13N_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS`          | JSON string array, not a PATH-separated string        |
| `A13N_ENVD_EXECUTION_UID`, `A13N_ENVD_EXECUTION_GID` | Paired positive integers for Linux required isolation |

Explicit disabled isolation accepts only host networking and no extra read-only paths. A successful disabled probe reports unavailable containment; it does not verify the outer sandbox for you.
