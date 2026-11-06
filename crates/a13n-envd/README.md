# a13n-envd

`a13n-envd` is the native Device daemon and Environment Interaction Protocol provider for Agent Foundation. The crate installs the `a13n-envd` binary.

## Device and Session runtime

The daemon implements EIP 0.4 over trusted stdio, authenticated HTTP(S), and outbound reverse WebSocket. One daemon owns a Device generation. Multiple independent Sessions select native or optional VM execution within its launch authority, with separate operations, process handles, transfers, receipts, and retained output.

- `initialize` negotiates the Device connection without creating a Session.
- `device.describe` and bounded `directory.list` work without a Session.
- `session.open.configuration` captures the Session boundary, working directory, command availability and lifecycle, then checks required methods.
- Session control and data frames carry the exact Session selector. Closing a Session cleans up only its resources, not its siblings or the Device.
- Explicit same-Session attachment can resume an existing owner before Session expiry with a newer owner epoch; opening a new Session never replays work or inherits process handles.

Device paths address the Host filesystem; Session paths address the selected native or guest filesystem. POSIX uses native absolute paths. Windows uses `/C:/...` and `/UNC/server/share/...`; directory discovery at `/` lists available volume roots. The fixed working directory is a default for commands and relative Host routing, not a filesystem access boundary.

The runtime supports bounded text and binary file operations, complete-candidate publication, structured foreground commands, background processes, stdin, retained stdout/stderr, cancellation, receipts, and local port observation. Opt-in [computer use](../../docs/a13n-envd/computer-use.md) adds screenshots and bounded desktop input on macOS, Linux X11, and Windows. It requires disabled Sandbox, inherited egress, and native desktop readiness. Each descriptor advertises exact methods and platform features. Session and aggregate Device quotas bound retained resources. A timeout after dispatch does not prove a mutation failed; clients must reconcile supported operations rather than blindly replay them.

## Launch configuration

Supply a trusted JSON file as an absolute path:

```bash
A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime \
  a13n-envd --config /absolute/path/to/envd.json
```

```json
{
  "device_id": "device-local",
  "default_working_directory": "/absolute/path/to/workspace",
  "directory_discovery": true,
  "trusted_executable_roots": ["/usr/bin", "/bin"],
  "shell_profiles": [
    {
      "profile_id": "sh",
      "display_name": "POSIX shell",
      "native_executable": "/bin/sh",
      "fixed_arguments": ["-c"],
      "safe_base_environment": {},
      "executable_search_roots": ["/usr/bin", "/bin"],
      "max_script_bytes": 1048576,
      "allow_login_mode": false
    }
  ]
}
```

The default directory must exist; when omitted, it is captured from the daemon startup directory. `A13N_ENVD_DEVICE_ID` can supply the Device identity when the JSON does not. Executable roots and shell profiles are trusted launch policy, not Session configuration. Set both to empty for a file-only Device.

`A13N_ENVD_TRANSPORT` defaults to `stdio` and also accepts `http` or `reverse_websocket`. HTTP requires a bind address, credential file, and either native TLS or explicit loopback/private-link plaintext scope. Reverse WebSocket requires a Host URL and credential file. The daemon exposes no inbound WebSocket, browser, generic HTTP, or health endpoint. Readiness is an EIP operation. Stdio stdout is reserved for protocol frames; diagnostics go to stderr.

Envd provides [read-only Device metrics](../../docs/a13n-envd/configuration.md#system-information-and-resource-monitoring) on Linux, macOS, and Windows over the existing EIP connection and authentication. The outer daemon collects cached CPU/memory, filesystem inventory, network/disk counters, socket counts and daemon usage, with optional cgroup v2 samples on Linux. Configure Prometheus or OTLP export when you need an external monitoring backend. `device.metrics` reads observations without opening a Session. The `connect` launcher enables default sampling; other launches enable it through `monitoring` configuration. The daemon retains the latest sample rather than a history.

The [configuration guide](../../docs/a13n-envd/configuration.md) owns all bootstrap fields and limits. The [Python client guide](../../docs/a13n-envd/python-client.md) shows shared Device ownership and independent Session scopes.

## Security and cleanup

Sessions use the configured execution identity and selected Sandbox/egress boundary. On Linux, an omitted execution UID/GID preserves the launcher identity; an explicit pair selects a provisioned account. Commands and file RPCs use that identity, while explicit sudo changes only the invoking command's authority. Linux deployments can opt into [Session egress](../../docs/a13n-envd/egress.md) for transparent destination policy and HTTPS header credential injection. Put mutually untrusted workloads behind separate Host-managed accounts, containers, VMs, or equivalent boundaries. Executable selection and resource accounting are not isolation.

Process cleanup owns the initial Unix process group or a non-breakaway Windows Job. Windows assigns a suspended child before execution and retains kill-on-close ownership. Platform limitations remain explicit in descriptors. Neither a process group nor a Job implies filesystem or network containment.

Complete-candidate writes publish via a same-filesystem rename after verification. They do not promise exclusive filesystem access or compare-and-swap: commands and external writers may race. The [operations guide](../../docs/a13n-envd/operations.md) explains capacity, output evidence, cleanup, and uncertain outcomes.

## Optional VM workers (Linux)

Build one or both embedded Rust SDK backends:

```bash
cargo build --locked -p a13n-envd --features vm-boxlite,vm-microsandbox
```

The default build remains native-only. VM execution requires Linux KVM, the selected SDK's runtime assets and image-registry access. BoxLite's build script prepares its runtime assets; follow that SDK's build prerequisites when building from source. Images must provide a compatible Linux userspace for the Envd executable and `/bin/sh`.

Pass this object as `session.open.configuration` on a Device with inherited networking and Full Control commands:

```json
{
  "working_directory": "/work",
  "isolation": {
    "mode": "vm",
    "backend": "boxlite",
    "image": "ubuntu:24.04",
    "cpus": 1,
    "memory_mib": 512,
    "mounts": [
      {"source": "/absolute/host/workspace", "target": "/work", "access": "read_write"}
    ]
  }
}
```

Use `microsandbox` for the other backend. Both run the ordinary Session engine as guest root and report guest POSIX paths and methods. Native Sessions and multiple VMs can coexist. Host environment variables are not inherited. Controlled/deny networking, nested native Sandbox and guest computer use are unsupported and fail without fallback.

VMs use standard upstream path-based mounts. Keep source paths and their ancestors stable during startup: concurrent replacement can expose a different Host object than the intended grant. The [execution contract](../../spec/a13n-envd/07-execution-isolation.md#vm-sessions) describes this risk and the deployment's responsibility. Read-only exports apply to file APIs and guest-root commands alike.

Session close, expiry or guest failure removes the VM, private runtime storage and registered upload staging. Daemon restart reclaims journaled VM state before publishing a new generation. Files published to Host mounts survive; private guest changes do not. Files moved or hardlinked outside registered staging are ordinary workspace data. Execution is never replayed after VM loss.

Opt-in real integration checks require the built backends and KVM:

```bash
strip -o target/debug/a13n-envd-vm-test target/debug/a13n-envd
python3 crates/a13n-envd/tests/vm_linux.py target/debug/a13n-envd-vm-test boxlite
python3 crates/a13n-envd/tests/vm_linux.py target/debug/a13n-envd-vm-test microsandbox
A13N_ENVD_TEST_BINARY="$PWD/target/debug/a13n-envd-vm-test" \
  A13N_ENVD_VM_TEST_BACKENDS=boxlite,microsandbox \
  uv run --locked pytest packages/a13n-envd-client/tests/eip/test_vm_e2e.py
```

The stripped copy avoids filling guest temporary storage during BoxLite bootstrap; keep Cargo's original binary intact.

## Rust integration boundary

`a13n_envd::eip` exposes generated request/result types, JSON and binary codecs, method metadata, and dispatch interfaces. The IDL and generator own these protocol artifacts. The daemon's transport and execution modules are private; use the binary for deployment and the Python client or an EIP implementation for remote access.

## Installation and validation

From the repository root, build the binary that matches this checkout's Python client:

```bash
cargo build --locked --package a13n-envd
./target/debug/a13n-envd --version
```

On Windows, the executable is `target\debug\a13n-envd.exe`.

For a published installation, [install the exact native release](../../docs/a13n-envd/installation.md) matching your `a13n-envd-client` version. `cargo install a13n-envd` without a version selects the latest crate, which may not match your client. Run `make eip-check` from the repository root to validate the generated protocol artifacts, native runtime, and Python integration tests.

## License

Licensed under the Apache License 2.0.
