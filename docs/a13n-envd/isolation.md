# Execution boundaries and troubleshooting

Envd applies a trusted Device policy to the complete Session worker: native execution identity, filesystem/process Sandbox, and egress mode. Commands, file RPCs and transfers use the same boundary; directory discovery uses the same grants and identity. The Host selects the policy and owns any outer container or VM. Envd owns worker preparation, readiness and cleanup.

## Isolation behavior

By default, Sandbox is disabled and networking is inherited, preserving the launcher's native identity and authority, including root. Restricted Sandbox instead exposes fixed directory grants plus a minimal read-only system view and private writable HOME/TMP/state. It composes with inherited, denied or [controlled egress](egress.md). Selecting cwd does not widen grants or change network mode. Sessions share granted files and are not mutually hostile tenants.

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

Grants must be existing absolute directories without overlaps. Changing grants requires another Device runtime, not a Session update. An empty grant list is valid, but cwd must still be accessible inside the resulting view.

| Platform | Restricted Sandbox                                                                           | Networking                                                                                           |
| -------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Linux    | bubblewrap, private process/filesystem view, capability dropping and privilege-gain blocking | inherit/deny work rootlessly with usable user namespaces; controlled requires the privileged backend |
| macOS    | Seatbelt via `sandbox-exec`, canonical path rules and inherited process restrictions         | inherit or deny; controlled is unsupported                                                           |

macOS does not provide Linux mount/PID namespaces or advertise `no_new_privs`: `privilege_gain_blocked` is false. Parent metadata may remain visible for path resolution, and the root directory itself is readable for native process startup; neither permits reading ungranted file contents. Keep grant paths stable while workers run. Linux binds checked directory handles. Unsupported combinations fail preparation.

### Native process ownership

| Platform | Process lifecycle                                                                                 |
| -------- | ------------------------------------------------------------------------------------------------- |
| POSIX    | Owned process groups, bounded termination and independent stdout/stderr retention                 |
| Windows  | Suspended process admission into an owned Job, descendant cleanup on exit/kill/timeout/owner loss |

Windows Job ownership is not filesystem or network confinement. Supported execution features report what is available; force kill is separate from graceful signal support. Use exact executable names, including `.exe` on Windows. Shell profiles are trusted startup configuration and must match the selected shell. PTY/ConPTY is not part of the command plane.

File paths address the Device namespace: POSIX `/work/file`, Windows `/C:/work/file` or `/UNC/server/share/file`. Windows `/` is a virtual directory-discovery root for volumes, not a command cwd. For ordinary Sessions, filesystem permissions, symlinks, links and platform access rules remain those of the execution account; native sudo is authorized by existing sudoers. Restricted workers enforce their platform's grant boundary for every path; controlled Linux workers additionally use private namespaces.

## Validate from this repository

```bash
make local-envd-test
make eip-test

# Native macOS worker/grants/network/lifecycle integration:
cargo test -p a13n-envd --test sandbox_macos -- --nocapture
```

The checks cover real daemon operation, Device/Session framing, native resource lifecycle, scoped cleanup, file transfers and Provider adaptation. Platform-specific tests require their actual native OS. No test in this package validates a deployment's external sandbox policy.

## Troubleshooting

- **Executable not found**: pass an absolute path to `resolve_a13n_envd_executable()`, set the Host's `A13N_ENVD_EXECUTABLE`, or install `a13n-envd` on `PATH`.
- **Release mismatch**: use the exact matching native daemon and Python client release. Do not confuse the release version with EIP's wire version.
- **Unknown bootstrap variable**: strip Host-only `A13N_ENVD_*` settings before launching the child. Use only variables documented in [configuration](configuration.md).
- **No command methods**: configure trusted executable roots or shell profiles and inspect `available_methods`.
- **Sudo fails**: Linux restricted Sandbox blocks privilege gain by design. With disabled Sandbox, `allow_sudo` defaults to true, but the image must provide sudo, a provisioned execution account and appropriate sudoers. Check outer `no_new_privs` and `nosuid` restrictions; Envd cannot override them. Without UID/GID settings, Linux execution retains the launcher's identity, including root. Use [trusted startup settings](configuration.md#native-identity-and-sudo) to select another provisioned account or disable additional privilege gains; disabling does not demote existing root execution.
- **Root HTTPS fails in a controlled Session**: native sudo may discard the Session CA environment. Preserve the relevant CA variables according to sudoers or configure the client trust store; see [Session egress](egress.md).
- **Invalid working directory**: choose an existing Device path, not a path on the requesting Host. Discovery requires no Session and can help select a directory.
- **A Session fails while others work**: inspect that Session's typed error and native evidence. Do not restart a healthy shared Device to repair one adapter.
- **Carrier loss**: pending effects can have unknown outcomes. Explicit same-Session attachment is limited by generation and disconnect grace; never replay mutations automatically.
- **Workspace disappeared**: normal adapter close deletes neither cwd nor arbitrary workspace files. Inspect the Host's lifecycle policy.
- **Runtime lock conflict**: one daemon owns one private runtime directory. Allocate a separate directory for a different launch.

## References

- [Environment Provider guide](../environments/index.md)
- [Daemon configuration](configuration.md)
- [Session and output lifecycle](operations.md)
- [Python EIP client](python-client.md)
