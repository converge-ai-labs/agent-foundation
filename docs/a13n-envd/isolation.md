---
title: Execution boundaries and troubleshooting
sidebarTitle: Execution boundaries
description: How Envd applies identity, filesystem, process, and egress policy to a Session, and how to diagnose failures.
---

Choose the execution account, filesystem grants, and network mode before launching Envd. These Device settings apply to every Session's commands, file operations, and transfers. Directory discovery uses the same identity and grants. The Host manages the outer account, container, or VM; Envd prepares and cleans up the Session workers.

## Isolation behavior

Start by choosing the filesystem view:

- **Disabled Sandbox** uses the execution account's native authority. By default, the account is the launcher's, including root, and networking is inherited.
- **Restricted Sandbox** exposes fixed directory grants, a minimal read-only system view, and private writable HOME/TMP/state. Windows also offers broad disk reads through `sandbox.full_disk_read: true`.

Choose inherited, denied, or [controlled egress](egress.md) separately, using a combination supported by the platform below. The working directory supplies defaults within that view; it does not widen grants or change networking. Sessions share granted files, so mutually untrusted workloads need separate outer deployments.

Inspect the Device and Session descriptors for the effective `boundary`, backend, and nonsecret policy digest. A published Session has completed required worker preparation. The Host must validate its own outer deployment. Boundary changes require a new runtime; commands cannot override the boundary, and unsupported combinations fail preparation without a weaker fallback.

### Restricted Sandbox example

This example gives commands read-write access to `/workspace`, read-only access to `/reference`, and no external network route.

1. Create `/workspace` and `/reference` on the Device.
2. Save the following as the daemon's JSON configuration.
3. Launch Envd with that file using [the configuration procedure](configuration.md#minimal-standalone-configuration), then open a Session in `/workspace`.

```json
{
  "full_control": true,
  "default_working_directory": "/workspace",
  "sandbox": {
    "mode": "restricted",
    "grants": [
      {"path": "/workspace", "access": "read_write"},
      {"path": "/reference", "access": "read_only"}
    ]
  },
  "egress": {"mode": "deny"}
}
```

Use existing absolute directories for grants and keep them nonoverlapping. Relaunch the daemon to change grants. An empty grant list is valid when the selected cwd is accessible in the resulting view.

| Platform | Restricted Sandbox                                                                           | Networking                                                                                                                                                                    |
| -------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Linux    | bubblewrap, private process/filesystem view, capability dropping and privilege-gain blocking | With restricted Sandbox, inherit/deny work rootlessly with usable user namespaces; deny with disabled Sandbox, and controlled, require the privileged backend (root launcher) |
| macOS    | Seatbelt via `sandbox-exec`, canonical path rules and inherited process restrictions         | inherit or deny; controlled is unsupported                                                                                                                                    |

On macOS, keep grant paths stable while workers run. Seatbelt permits parent metadata needed for path resolution and root-directory reads needed for native startup; ungranted file contents remain denied. It provides neither Linux mount/PID namespaces nor `no_new_privs`, so `privilege_gain_blocked` is false. Linux binds checked directory handles.

### Native Windows MXC

Windows restricted Sandbox uses the experimental Microsoft eXecution Container (MXC) Process Security Environment backend, reported as `windows_mxc`. It supports inherit and deny networking. Envd probes native creation, startup-info preparation, and closure with the chosen network capabilities and peer policy. Use a host that passes this probe; a Windows version number alone does not establish PSEC support. WSL uses the Linux backend.

The default view includes read-only Windows and Program Files directories, the Envd executable, and private writable HOME/USERPROFILE/TEMP/state. To allow broader reads, set `sandbox.full_disk_read: true`. Writes remain confined to read-write grants and private worker state, but commands can read account-accessible Host configuration and credentials on disk. Choose this option only when that read access is intended. Envd continues to deny its own bootstrap configuration, transport credentials, and broker runtime directory.

Broad reads default to false in Envd and true in Harness UI on Windows. Linux and macOS reject this option. Mapped network drives are excluded. The volume-root read view is fixed when the worker starts; open a new Session to include newly attached local volumes or new top-level entries. Existing granted directories keep their normal access rules.

Keep grant ancestor paths stable. Envd retains checked grant directory handles without delete sharing for the worker lifetime. PowerShell startup uses a private desktop and read-only registry access, without ACL mutation or elevation. The descriptor reports `privilege_gain_blocked: false`; MXC does not provide Linux's `no_new_privs` guarantee.

Validate the build tools you will use on the target host. The native acceptance suite exercises PowerShell, Git, and Python with broad reads. Node.js 24.21 `fs.realpathSync.native` can fail with `EPERM` under MXC.

For source-checkout acceptance, install Rust, Python, uv, Git, and PowerShell on a native Windows host. Run these commands from the repository root:

```powershell
cargo build -p a13n-envd --locked
cargo test -p a13n-envd native_mxc_probe --locked -- --ignored --nocapture
uv sync --locked --all-packages
$env:A13N_ENVD_TEST_BINARY = (Resolve-Path target/debug/a13n-envd.exe).Path
uv run --no-sync pytest packages/a13n-harness-ui/tests/test_windows_sandbox.py -v
```

The native probes check inherit/deny networking, IPv4 loopback, IPv6 loopback when available, and ungranted file-read denial. The UI suite checks readiness, broad reads with confined writes, protected broker files, Project and Thread-file permissions, file operations, and Session/process cleanup. It also covers junction confinement, no-follow deletion, private environment, broker loss during a command, and replaced grants. Selecting the binary makes missing MXC support a test failure.

Exercise Session-open cancellation on the target Windows build as well. Cross-compilation cannot validate kernel cleanup. When cleanup is unconfirmed, worker state and active process charges remain retained until Device restart.

### Native process ownership

| Platform | Process lifecycle                                                                                 |
| -------- | ------------------------------------------------------------------------------------------------- |
| POSIX    | Owned process groups, bounded termination and independent stdout/stderr retention                 |
| Windows  | Suspended process admission into an owned Job, descendant cleanup on exit/kill/timeout/owner loss |

Use the advertised execution features to choose process controls: force kill and graceful signals are separate capabilities. A Windows Job owns process cleanup; filesystem and network confinement come from the selected Sandbox and egress policy. Pass exact executable names, including `.exe` on Windows, and configure shell profiles for the selected shell. The command plane has no PTY/ConPTY support.

Address files in the Device namespace: `/work/file` on POSIX, or `/C:/work/file` and `/UNC/server/share/file` on Windows. Windows `/` lists volumes for directory discovery; choose an actual directory for command cwd.

With Sandbox disabled, filesystem permissions, links, and native sudo follow the execution account and existing sudoers. Restricted workers enforce platform grants for every path. Controlled Linux workers also use private namespaces.

## Validate from this repository

From a source checkout with the repository's development dependencies installed, run:

```bash
make local-envd-test
make eip-test

# Native macOS worker/grants/network/lifecycle integration:
cargo test -p a13n-envd --test sandbox_macos -- --nocapture
```

These checks cover daemon operation, Device/Session framing, resource lifecycle, cleanup, transfers, and Provider adaptation. Run platform-specific tests on their native OS. Validate the deployment's outer sandbox policy separately.

## Troubleshooting

Start with the failing Session's typed error, then check the relevant setting:

- **Executable not found**: select an absolute path with `resolve_a13n_envd_executable()`, set the Host's `A13N_ENVD_EXECUTABLE`, or install `a13n-envd` on `PATH`.
- **Release mismatch**: match the native daemon and Python client release exactly. EIP's wire version is a separate identifier.
- **Unknown bootstrap variable**: remove Host-only `A13N_ENVD_*` settings from the child's environment. Pass the variables listed in [configuration](configuration.md).
- **No command methods**: enable Full Control or configure trusted executable roots or shell profiles, then inspect `available_methods`.
- **Sudo fails**: check the selected Sandbox first. Linux restricted Sandbox blocks privilege gain. With Sandbox disabled, `allow_sudo` defaults to true and still requires installed sudo, a provisioned account, and suitable sudoers. Outer `no_new_privs` or `nosuid` restrictions continue to apply. Use [native identity settings](configuration.md#native-identity-and-sudo) to select an account; without UID/GID settings, the launcher identity is retained, including root. Disabling further privilege gain does not demote root.
- **Root HTTPS fails in a controlled Session**: check whether sudo removed the Session CA variables. Preserve them according to sudoers or configure the client's trust store; see [Session egress](egress.md).
- **Invalid working directory**: use directory discovery to select an existing Device path. Discovery works before opening a Session.
- **One Session fails while others work**: investigate that Session's error and native evidence while leaving the shared Device running.
- **Carrier loss**: reconcile pending effects using the original operation evidence. Same-Session attachment requires the original generation and an unexpired grace period; it does not authorize mutation replay.
- **Files disappeared**: inspect the Host's lifecycle policy. Normal adapter close preserves cwd and workspace files.
- **Runtime lock conflict**: allocate a different private runtime directory for each daemon launch.

## References

- [Environment Provider guide](../environments/index.md)
- [Daemon configuration](configuration.md)
- [Session and output lifecycle](operations.md)
- [Python EIP client](python-client.md)
