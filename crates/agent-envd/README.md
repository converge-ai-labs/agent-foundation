# agent-envd

`agent-envd` is the environment daemon and Environment Interaction Protocol provider for Agent Foundation. It is distributed as the `agent-envd` crate and installs the `agent-envd` binary.

## Current runtime profile

The daemon implements the canonical EIP 1.0 protocol over trusted stdio, a dedicated authenticated HTTP(S) listener, and outbound reverse WebSocket. Its current surface includes:

- initialization, environment description, session close, cancellation, operation receipts, and local port observation;
- trusted configured mounts for text reads, metadata, listing, bounded find and search, and binary streaming reads;
- complete-candidate text and binary publication, directory creation, patch, copy, move, and remove on eligible writable mounts;
- structured foreground commands and generation-owned background processes through one gated supervisor and backend lifecycle owner;
- bounded stdin, stdout, stderr, live retained-output references, non-draining explicit process-output offsets, advertised semantic signals, kill, wait, and release;
- bounded operation, process, transfer, candidate, receipt, and retained-output state;
- correlated concurrent requests, fresh generations, strict framing and envelope validation, finite relative timeouts, cancellation, and typed errors.

Every descriptor reports exact JSON-RPC `available_methods`; initialization checks exact `required_methods` rather than capability families. Typed `execution_features` separately reports optional command-limit, per-command-network-deny, and individual signal-action support. Required execution uses a probed deny-default bubblewrap backend on Linux or Seatbelt backend on macOS and supports per-command network narrowing; explicit outer-host mode reports optional command/network containment features false. Unix advertises distinct interrupt/terminate actions, while non-Unix omits `process.signal` until native semantics exist. Availability is derived from configured mounts, command policy, isolation probe, and truthful platform support, so one unavailable method does not hide adjacent methods. Complete-candidate atomic publication is currently available on Linux and macOS. It does not imply exclusive filesystem control or compare-and-swap: commands and external writers can race with envd operations.

When command policy and a command-enabled mount are configured, the descriptor reports the exact available shell, process, and output methods plus its logical shell profiles. Foreground and background execution share one transactional start gate; `process.start` publishes a handle only after requested-executable spawn succeeds. The same owner drains stdout and stderr concurrently, enforces finite limits, targets the backend-managed command lifecycle for control and cleanup, and drains remaining managed commands during daemon shutdown. In explicit `disabled` mode, native cleanup covers the initial Unix process group and a best-effort Windows task tree; descendants outside that platform-native target remain the outer Host's responsibility, as reported by `process_containment=false` and `cleanup_guarantee=outer_host`. Required native isolation is implemented on Linux and macOS; Windows remains fail-closed until its backend is delivered. Only the HTTP profile binds inbound resources, limited to authenticated `/eip/control` and `/eip/transfer`; envd exposes no inbound WebSocket, generic HTTP, browser, health, or readiness surface.

## Launch configuration

The standalone daemon requires only its Environment identity for the default stdio profile:

```text
AGENT_ENVD_ENVIRONMENT_ID=<provider-owned stable identity>
```

Execution isolation defaults to `required`. On Linux it uses the fixed OS-managed `/usr/bin/bwrap` helper, fresh user/mount/PID/IPC/UTS namespaces, a synthetic filesystem, private `/proc` and `/dev`, and optional network namespaces. On macOS it uses `/usr/bin/sandbox-exec`, private command home and temporary roots, and reviewed system/toolchain runtime roots. Windows continues to fail closed in required mode until its native backend is delivered. The packaged sandbox container image explicitly sets `AGENT_ENVD_EXECUTION_ISOLATION=disabled`, delegating inner command containment to the outer container.

Command execution additionally requires an absolute private runtime directory:

```text
AGENT_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
```

`AGENT_ENVD_TRANSPORT` defaults to `stdio` and also accepts `http` or `reverse_websocket`. HTTP requires `AGENT_ENVD_HTTP_BIND`, `AGENT_ENVD_HTTP_CREDENTIAL_FILE`, and either paired native TLS files or `AGENT_ENVD_HTTP_PLAINTEXT_SCOPE=loopback|provider_private_link`. Reverse WebSocket requires `AGENT_ENVD_REVERSE_WS_URL` and `AGENT_ENVD_REVERSE_WS_CREDENTIAL_FILE`; an optional CA file adds deployment trust for `wss`. Configured mounts are the only filesystem roots. Protected runtime roots are subtracted from all overlapping mount lookups.

Explicit `disabled` mode delegates containment to the outer Host, emits one structured startup warning on stderr, and must be used only inside an appropriate provider sandbox or test boundary. `agent-envd isolation probe --json` runs the selected backend's production preflight without admitting a carrier.

### Linux host prerequisites

Required Linux isolation needs the distribution's non-setuid `bubblewrap` package installed at `/usr/bin/bwrap`. The Host kernel and active Linux Security Modules must allow the daemon user to create unprivileged user namespaces. Envd verifies the helper and the complete configured isolation posture before admitting a carrier; it exits fail-closed if the helper, user namespaces, protected-path subtraction, PID cleanup, keyring isolation, or network posture does not work. Every required-isolation preflight error preserves the native cause and explains that `AGENT_ENVD_EXECUTION_ISOLATION=disabled` is valid only when a trusted outer sandbox owns command containment. Envd never changes a sysctl, loads an AppArmor policy, searches `PATH`, uses a setuid helper, or falls back to `disabled`.

Linux `network="host"` deliberately shares the Host network namespace, including abstract Unix-socket endpoints in that namespace. Pathname sockets remain absent unless they are inside an authorized projection. Use daemon-wide or per-command `network="deny"` when payloads must not reach Host network-namespace IPC.

Ubuntu 24.04 can have `kernel.apparmor_restrict_unprivileged_userns=1`, which denies user namespaces unless an AppArmor profile explicitly grants `userns` to the relevant executable. Production Hosts should install a narrowly scoped, operator-reviewed AppArmor profile for `/usr/bin/bwrap` rather than disable the restriction globally. For a temporary diagnostic on a trusted development machine, an administrator can run:

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
agent-envd isolation probe --json
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=1
```

The first command weakens that Host-wide AppArmor restriction until it is restored or the Host reboots; it is not an envd setup action. Deployments that already provide an outer container, VM, or equivalent sandbox should leave the outer boundary responsible and explicitly select `AGENT_ENVD_EXECUTION_ISOLATION=disabled`.

An optional paired payload identity is available only for required Linux isolation:

```text
AGENT_ENVD_EXECUTION_UID=<positive-uid>
AGENT_ENVD_EXECUTION_GID=<positive-gid>
```

Both values must be supplied together. They are trusted bootstrap configuration and cannot be selected by an EIP request.

In stdio mode, stdin and stdout are reserved exclusively for framed EIP traffic; startup and runtime diagnostics use stderr.

An optional trusted JSON file configures mounts and is supplied as an absolute path:

```bash
agent-envd --config /absolute/path/to/agent-envd.json
```

A configured writable mount needs only its existing native root. Envd creates a randomly named hidden candidate in the destination directory, verifies the complete bytes, and publishes it with a same-filesystem rename:

```json
{
  "root_mount_id": "workspace",
  "execution": {
    "isolation": "required",
    "network": "host",
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

The native root must already exist. `root_mount_id` is inferred with exactly one mount, is absent with no mounts, and is required when multiple mounts are configured. Omitting `allowed_operations` enables the operations appropriate for the mount; specifying it narrows the policy.

Command policy supplies fixed executable search roots and optional trusted shell profiles. A command-enabled mount must allow `command_cwd`; an `ExecutablePath` selected from that mount additionally requires `executable_source`:

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

Payloads receive a finite allowlist of ordinary locale, terminal, certificate, and language-tool compatibility variables from daemon startup. Request `set` and `unset` values apply after that layer. Envd always replaces `PATH`, `HOME`, and temporary-directory variables and removes daemon control state, `AGENT_ENVD_*`, ambient credentials, and dynamic-loader variables.

## Installation

```bash
cargo install agent-envd
```

## License

Licensed under the Apache License 2.0.
