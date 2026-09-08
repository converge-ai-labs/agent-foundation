# a13n-envd

`a13n-envd` is the native Environment Interaction Protocol (EIP) daemon. It serves one configured Environment identity and one process generation over trusted stdio, authenticated HTTP(S), or outbound reverse WebSocket.

Most Harness applications should use the built-in `a13n.local-envd` Environment Provider. The Provider owns process launch, private runtime allocation, readiness, exact client/daemon compatibility, and cleanup. Use the standalone daemon configuration in this guide when implementing or operating a provider boundary.

## What it provides

A configured daemon can expose:

- bounded text and binary file reads;
- listing, metadata, find, and search;
- atomic complete-candidate file publication and filesystem mutations;
- foreground commands and generation-owned background processes;
- bounded stdin, output, signals, wait, kill, and release;
- retained process output and local port observations;
- correlated concurrent requests, cancellation, operation receipts, typed errors, and readiness.

The descriptor advertises exact available EIP methods and optional execution features. Availability depends on configured mounts, operation policy, command policy, isolation readiness, and platform support.

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

## Recommended Harness path

Resolve the built-in Local Envd Provider, construct one fresh Environment, and pass that adapter to a Harness Run:

```python
from pathlib import Path

from a13n_environment import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    build_environment_provider_catalog,
    resolve_a13n_envd_executable,
)

catalog = build_environment_provider_catalog(
    builtin_keys=("a13n.local-envd",),
)
provider = catalog.require("a13n.local-envd")
configuration = provider.validate_configuration(
    schema_version="1",
    value={
        "environment_id": "sandbox",
        "workspace": {"path": str(Path("./workspace").resolve())},
        "execution_network": "deny",
    },
)
environment = provider.create_environment(
    configuration=configuration,
    state=None,
    runtime=LocalEnvdProviderRuntime(
        executable=resolve_a13n_envd_executable(),
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
    ),
)

result = await executable.run(
    "Inspect the workspace",
    environment=environment,
)
```

Each Local Envd adapter owns one daemon generation, one private runtime allocation, one reusable stdio carrier, and one EIP session while entered. Harness close stops the daemon and removes only its private runtime, not the selected workspace. Construct another fresh adapter for every independent Run.

## Isolation behavior

Execution isolation defaults to `required` and fails closed when its native backend is unavailable.

| Platform | Required backend                                   | Current behavior                                               |
| -------- | -------------------------------------------------- | -------------------------------------------------------------- |
| Linux    | `/usr/bin/bwrap` with unprivileged user namespaces | Supported; complete posture is probed before carrier admission |
| macOS    | `/usr/bin/sandbox-exec` using Seatbelt profiles    | Supported                                                      |
| Windows  | Native backend not yet delivered                   | `required` fails closed                                        |

Run the production isolation preflight after supplying the same trusted bootstrap configuration used for launch:

```bash
a13n-envd isolation probe --json
```

Set `A13N_ENVD_EXECUTION_ISOLATION=disabled` only when a trusted outer container, VM, or equivalent boundary owns command containment. Disabled mode is explicit, reports containment features as unavailable, and emits a startup warning. It is not a fallback for a failed required-isolation probe.

On Linux, install the distribution's non-setuid Bubblewrap package at `/usr/bin/bwrap`. The kernel and active Linux Security Modules must allow the daemon user to create unprivileged user namespaces. `a13n-envd` does not change sysctls, load AppArmor policy, search `PATH` for the helper, or fall back to disabled mode.

### Windows: client support versus execution isolation

EIP is platform-neutral. A Windows client can connect to a Linux or macOS daemon and use the capabilities that daemon actually advertises. It does not make the remote execution host Windows, nor add client-side isolation. Harness UI's built-in Sandbox profile uses Local Envd on every OS; App readiness delegates to the installed daemon's production probe rather than maintaining a second OS support policy. Until the native required backend is available, Windows daemon file and command operations below require an explicitly configured, trusted outer sandbox with `disabled`; the default startup does not admit even file-only sessions.

| Surface                                                      | Windows behavior                                                                                                                                               |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| EIP control, descriptors, file operations and transfers      | Available with configured mount authority; native paths remain private and EIP uses logical mount-relative paths                                               |
| Native `required` execution                                  | Not yet implemented; daemon startup and Local Envd preflight fail closed                                                                                       |
| Explicit `disabled` execution inside a trusted outer sandbox | Structured argv, configured shell profiles, stdin, separate output streams, status, wait and kill                                                              |
| Command-tree ownership                                       | A suspended initial process enters a non-breakaway, kill-on-close Job before release; normal root exit, kill, timeout and supervisor loss clean up descendants |
| Cleanup evidence                                             | Completion requires the owned Job to report no active processes, not successful `taskkill` or root exit alone                                                  |
| Filesystem and network confinement                           | Job ownership does not provide either; disabled posture remains `outer_host` with containment fields false                                                     |
| Interrupt / graceful terminate                               | Not advertised on Windows; force kill remains separate                                                                                                         |
| POSIX executable bits                                        | Reading reports false; setting true is unsupported, not a Windows execution-permission change                                                                  |
| PTY / ConPTY                                                 | Not part of the EIP command plane on any OS                                                                                                                    |
| Per-process memory, CPU and descendant-count limits          | Not advertised by current backends; wall time, stdin, output, spool and command-admission limits still apply                                                   |

Use exact executable names, including `.exe`; envd does not perform `PATHEXT` expansion. Shell profiles are trusted bootstrap and must use arguments appropriate to the selected shell. A Windows binary or passing protocol tests does not establish AppContainer/token, ACL, reparse-point, network-denial or full sandbox conformance. Required Windows support is not enabled until those boundaries pass native production probing.

### Ubuntu 24.04 and AppArmor user namespaces

Ubuntu 24.04 commonly enables `kernel.apparmor_restrict_unprivileged_userns=1`. Even with `kernel.unprivileged_userns_clone=1`, an unprofiled Bubblewrap process can be denied permission to write its UID/GID map. A typical failure is:

```text
bwrap: setting up uid map: Permission denied
```

Envd adds namespace/AppArmor guidance to this failure and still refuses required-isolation startup. A bare-host Local Envd Provider cannot honestly offer its sandbox when that probe fails. HTTP or WebSocket connectivity alone does not solve isolation prerequisites on the machine running the daemon.

Check the relevant policy and kernel evidence:

```bash
sysctl kernel.apparmor_restrict_unprivileged_userns \\
  kernel.unprivileged_userns_clone user.max_user_namespaces
sudo journalctl -k --since '-10 min' | grep -E 'apparmor|userns|uid_map'
```

**Recommended: keep the global restriction and permit user namespaces for the fixed `/usr/bin/bwrap` executable.** First check for a distribution-provided profile; update that profile rather than installing a second profile matching the same executable. If none exists, an administrator can install the following AppArmor 4 profile on Ubuntu 24.04:

```bash
sudo tee /etc/apparmor.d/a13n-bwrap >/dev/null <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile a13n-bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
}
EOF
sudo apparmor_parser -r /etc/apparmor.d/a13n-bwrap
```

This gives the previously unconfined executable an explicit profile permitting user namespaces. It is not itself a restrictive sandbox profile; Bubblewrap still establishes the filesystem, process and selected network isolation that envd probes. The permission applies to other users of that executable too. Keep Bubblewrap root-owned, non-setuid and non-writable by ordinary users. Do not apply these commands over a more restrictive existing profile without reviewing that policy.

Validate as the ordinary account that runs envd, **not with sudo**:

```bash
bwrap --unshare-user --unshare-pid \\
  --ro-bind / / --proc /proc --dev /dev /bin/true
a13n-envd isolation probe --json
```

A successful Bubblewrap command is only a quick prerequisite check. The production-equivalent envd probe is the acceptance check. Containers or other LSM policies may impose additional restrictions even after the AppArmor profile is installed. Envd does not change any host policy automatically and never downgrades to Direct Local or disabled isolation.

To remove only the profile installed above:

```bash
sudo apparmor_parser -R /etc/apparmor.d/a13n-bwrap
sudo rm /etc/apparmor.d/a13n-bwrap
```

#### Optional global opt-out

An administrator who explicitly accepts the broader host policy change can disable this AppArmor restriction for **all unprivileged processes**. This is not required when the per-executable profile works, and it is different from disabling envd's command isolation.

Temporary, effective immediately until reboot or another sysctl update:

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
```

For persistence across reboot:

```bash
sudo tee /etc/sysctl.d/99-local-userns.conf >/dev/null <<'EOF'
kernel.apparmor_restrict_unprivileged_userns = 0
EOF
sudo sysctl -p /etc/sysctl.d/99-local-userns.conf
```

This does not disable the rest of AppArmor. To restore the restriction after using the persistent configuration above:

```bash
sudo rm /etc/sysctl.d/99-local-userns.conf
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=1
```

For a managed Docker Envd deployment, the outer container normally owns containment and the Docker Provider explicitly disables envd's inner isolation. Do not use that setting as a workaround on an otherwise unsandboxed bare host.

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

Native mount roots must already exist. With one mount, `root_mount_id` can be inferred; with multiple mounts it is required when Environment-relative routing needs a default. Omitting `allowed_operations` enables the operations appropriate for that mount, while specifying it narrows the policy.

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

## Validate from this repository

```bash
make local-envd-test
make eip-test
```

`make local-envd-test` builds the daemon and tests the real Local Envd Provider path. `make eip-test` also runs generation, client, wire, cross-language, integration, and Rust daemon tests.

## Troubleshooting

- **The executable is not found**: pass an absolute path to `resolve_a13n_envd_executable()`, set `A13N_ENVD_EXECUTABLE`, or install `a13n-envd` on `PATH`.
- **Required isolation fails on Linux**: verify `/usr/bin/bwrap`, unprivileged user namespaces, and active LSM policy; run the JSON isolation probe with production-equivalent configuration.
- **Required isolation fails on Windows**: use a supported platform or a trusted outer sandbox with explicit disabled mode until the native backend is delivered.
- **No command methods are advertised**: configure a command-enabled mount, `command_cwd`, executable roots or shell profiles, and a usable isolation posture.
- **A method is unavailable**: inspect the exact descriptor `available_methods` and execution features rather than inferring broad capability families.
- **The workspace disappeared after a run**: Local Envd removes its private runtime only. Workspace removal indicates external lifecycle policy, not normal adapter close.

## References

- [Environment overview](../environments/index.md)
- [Environment Provider guide](../a13n-environment/index.md)
- [`a13n-envd` crate README](https://github.com/converge-ai-labs/agent-foundation/tree/main/crates/a13n-envd)
- [`a13n-envd-client` README](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-envd-client)
- [Normative a13n-envd and EIP specifications](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/a13n-envd)
