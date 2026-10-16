---
title: Execution boundaries and troubleshooting
sidebarTitle: Execution boundaries
description: How Envd applies identity, filesystem, process, and egress policy to a Session, and how to diagnose failures.
---

Envd applies a trusted Device policy to the complete Session worker: native execution identity, filesystem/process Sandbox, and egress mode. Commands, file RPCs and transfers use the same boundary; directory discovery uses the same grants and identity. The Host selects the policy and owns any outer container or VM. Envd owns worker preparation, readiness and cleanup.

## Isolation behavior

By default, Sandbox is disabled and networking is inherited, preserving the launcher's native identity and authority, including root. Restricted Sandbox exposes fixed directory grants plus a minimal read-only system view and private writable HOME/TMP/state. Windows can additionally enable broad disk reads with `sandbox.full_disk_read: true`. It composes with inherited, denied or [controlled egress](egress.md). Selecting cwd does not widen grants or change network mode. Sessions share granted files; Envd does not isolate Sessions from each other.

There are no per-command boundary overrides or fallback to weaker execution. Device and Session descriptors expose the effective `boundary`, backend and nonsecret policy digest. Session publication follows required worker preparation; this is not attestation of the outer deployment.

### Restricted Sandbox example

Provision the directories first, then launch with:

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

Grants must be existing absolute directories without overlaps. Changing grants requires a new daemon launch with the new grants, not a Session update. An empty grant list is valid, but cwd must still be accessible inside the resulting view.

| Platform | Restricted Sandbox                                                                           | Networking                                                                                                                                                                    |
| -------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Linux    | bubblewrap, private process/filesystem view, capability dropping and privilege-gain blocking | With restricted Sandbox, inherit/deny work rootlessly with usable user namespaces; deny with disabled Sandbox, and controlled, require the privileged backend (root launcher) |
| macOS    | Seatbelt via `sandbox-exec`, canonical path rules and inherited process restrictions         | inherit or deny; controlled is unsupported                                                                                                                                    |

macOS does not provide Linux mount/PID namespaces or advertise `no_new_privs`: `privilege_gain_blocked` is false. Parent metadata may remain visible for path resolution, and the root directory itself is readable for native process startup; neither permits reading ungranted file contents. Keep grant paths stable while workers run. Linux binds checked directory handles. Unsupported combinations fail preparation.

### Native Windows MXC

Windows restricted Sandbox uses the experimental Microsoft eXecution Container (MXC) Process Security Environment backend (`windows_mxc`) with inherit or deny networking. Envd probes real native creation, startup-info preparation and closure with the selected network capabilities and peer policy. A matching Windows version string alone is insufficient: the OS must expose the PSEC capabilities required by the selected network mode. Unsupported hosts fail without fallback. WSL uses the Linux backend.

The default runtime view includes read-only Windows and Program Files directories, the Envd executable, and private writable HOME/USERPROFILE/TEMP/state. `sandbox.full_disk_read: true` adds Codex-style broad reads of available volumes while preserving writes only to read-write grants and private worker state. This permits reading account-accessible Host configuration and credentials on disk; it is a write-confinement policy, not a confidentiality boundary for Host files. Envd explicitly denies its bootstrap configuration, transport credentials and broker runtime directory. The option defaults to false in Envd and is rejected on Linux/macOS; Harness UI enables it by default on Windows. Keep grant ancestor paths stable. Envd retains checked grant directory handles without delete sharing while workers run. It uses a private desktop for PowerShell startup, read-only registry access, and no ACL mutation or elevation. `privilege_gain_blocked` is false; this is not the Linux `no_new_privs` guarantee.

Broad reads exclude mapped network drives. Each worker fixes its volume-root read view at startup; open a new Session to include newly attached local volumes or newly created top-level entries. Existing granted directories retain their normal access rules.

Native runtime compatibility depends on PSEC support in the application. PowerShell, Git and Python have been exercised with broad reads; Node.js 24.21 `fs.realpathSync.native` can fail with `EPERM` under MXC. Validate your build tools on the target host.

For source-checkout acceptance on a native Windows machine with Rust, Python, uv and PowerShell, run from the repository root:

```powershell
cargo build -p a13n-envd --locked
cargo test -p a13n-envd native_mxc_probe --locked -- --ignored --nocapture
uv sync --locked --all-packages
$env:A13N_ENVD_TEST_BINARY = (Resolve-Path target/debug/a13n-envd.exe).Path
uv run --no-sync pytest packages/a13n-harness-ui/tests/test_windows_sandbox.py -v
```

The native probe tests exercise IPv4 loopback and IPv6 loopback when available, verify inherit/deny behavior, and reject ungranted file reads. The UI opt-in suite checks production readiness, broad reads with outside writes denied, broker-file denial, read-only Projects and attachments, writable Thread tmp, file listing/search/copy/move/transfers, junction write confinement and no-follow deletion, private environment, Session cleanup, broker termination during an active command and replaced grant rejection. Once the binary is selected, missing MXC support fails the test. Also exercise Session-open cancellation on the target Windows build; cross-compilation does not verify these kernel lifecycles. If cleanup is unconfirmed, worker state and active process charges remain retained until Device restart.

### Native process ownership

| Platform | Process lifecycle                                                                                 |
| -------- | ------------------------------------------------------------------------------------------------- |
| POSIX    | Owned process groups, bounded termination and independent stdout/stderr retention                 |
| Windows  | Suspended process admission into an owned Job, descendant cleanup on exit/kill/timeout/owner loss |

Windows Job ownership is not filesystem or network confinement. Supported execution features report what is available; force kill is separate from graceful signal support. Use exact executable names, including `.exe` on Windows. Shell profiles are trusted startup configuration and must match the selected shell. PTY/ConPTY is not part of the command plane.

File paths address the Device namespace: POSIX `/work/file`, Windows `/C:/work/file` or `/UNC/server/share/file`. Windows `/` is a virtual directory-discovery root for volumes, not a command cwd. With disabled Sandbox, filesystem permissions, symlinks, links and platform access rules remain those of the execution account; native sudo is authorized by existing sudoers. Restricted workers enforce their platform's grant boundary for every path; controlled Linux workers additionally use private namespaces.

## Validate from this repository

```bash
make local-envd-test
make eip-test

# Native macOS worker/grants/network/lifecycle integration:
cargo test -p a13n-envd --test sandbox_macos -- --nocapture
```

The checks cover real daemon operation, Device/Session framing, native resource lifecycle, scoped cleanup, file transfers and Provider adaptation. Platform-specific tests require their actual native OS. No test in this repository validates a deployment's external sandbox policy.

## Troubleshooting

- **Executable not found**: pass an absolute path to `resolve_a13n_envd_executable()`, set the Host's `A13N_ENVD_EXECUTABLE`, or install `a13n-envd` on `PATH`.
- **Release mismatch**: use the exact matching native daemon and Python client release. Do not confuse the release version with EIP's wire version.
- **Unknown bootstrap variable**: strip Host-only `A13N_ENVD_*` settings before launching the child. Use only variables documented in [configuration](configuration.md).
- **No command methods**: enable Full Control, or configure trusted executable roots or shell profiles. Then inspect `available_methods`.
- **Sudo fails**: Linux restricted Sandbox blocks privilege gain by design. With disabled Sandbox, `allow_sudo` defaults to true, but the image must provide sudo, a provisioned execution account and appropriate sudoers. Check outer `no_new_privs` and `nosuid` restrictions; Envd cannot override them. Without UID/GID settings, Linux execution retains the launcher's identity, including root. Use [trusted startup settings](configuration.md#native-identity-and-sudo) to select another provisioned account or disable additional privilege gains; disabling does not demote existing root execution.
- **Root HTTPS fails in a controlled Session**: native sudo may discard the Session CA environment. Preserve the relevant CA variables according to sudoers or configure the client trust store; see [Session egress](egress.md).
- **Invalid working directory**: choose an existing Device path, not a path on the requesting Host. Discovery requires no Session and can help select a directory.
- **A Session fails while others work**: inspect that Session's typed error and native evidence. Do not restart a healthy shared Device to repair one adapter.
- **Carrier loss**: pending effects can have unknown outcomes. Explicit same-Session attachment is limited by generation and disconnect grace; never replay mutations automatically.
- **Files disappeared**: normal adapter close deletes neither the cwd nor any other files. Inspect the Host's lifecycle policy.
- **Runtime lock conflict**: one daemon owns one private runtime directory. Allocate a separate directory for a different launch.

## References

- [Environment Provider guide](../environments/index.md)
- [Daemon configuration](configuration.md)
- [Session and output lifecycle](operations.md)
- [Python EIP client](python-client.md)
