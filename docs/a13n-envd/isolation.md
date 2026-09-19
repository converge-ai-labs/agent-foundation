# Outer security and troubleshooting

Envd is an execution service for its operating-system account. It does not create a filesystem or network sandbox around each command. The Host or operator must place the **entire daemon** inside any selected container, VM, account boundary or OS sandbox before connecting it to an Agent.

## Isolation behavior

A Device connection and its Sessions share the daemon's outer authority. Sessions isolate resource ownership and correlation, not tenants. Selecting a working directory neither restricts file access to that directory nor limits commands to it. Directory discovery reveals the directories accessible to the Device; disable it when the Host does not want to offer browsing.

There is no per-command `required`/`disabled` isolation setting, mount policy, network override or production isolation probe. Old configuration fields are rejected rather than interpreted as compatibility aliases. A Host that promises a Sandbox must verify its own launcher and outer boundary; successful EIP readiness is not proof of sandboxing.

### Native process ownership

| Platform | Process lifecycle                                                                                 |
| -------- | ------------------------------------------------------------------------------------------------- |
| POSIX    | Owned process groups, bounded termination and independent stdout/stderr retention                 |
| Windows  | Suspended process admission into an owned Job, descendant cleanup on exit/kill/timeout/owner loss |

Windows Job ownership is not filesystem or network confinement. Supported execution features report what is available; force kill is separate from graceful signal support. Use exact executable names, including `.exe` on Windows. Shell profiles are trusted startup configuration and must match the selected shell. PTY/ConPTY is not part of the command plane.

File paths address the Device namespace: POSIX `/work/file`, Windows `/C:/work/file` or `/UNC/server/share/file`. Windows `/` is a virtual directory-discovery root for volumes, not a command cwd. Filesystem permissions, symlinks, links and platform access rules remain those of the daemon account.

## Validate from this repository

```bash
make local-envd-test
make eip-test
```

The checks cover real daemon operation, Device/Session framing, native resource lifecycle, scoped cleanup, file transfers and Provider adaptation. Platform-specific tests require their actual native OS. No test in this package validates a deployment's external sandbox policy.

## Troubleshooting

- **Executable not found**: pass an absolute path to `resolve_a13n_envd_executable()`, set the Host's `A13N_ENVD_EXECUTABLE`, or install `a13n-envd` on `PATH`.
- **Release mismatch**: use the exact matching native daemon and Python client release. Do not confuse the release version with EIP's wire version.
- **Unknown bootstrap variable**: strip Host-only `A13N_ENVD_*` settings before launching the child. Use only variables documented in [configuration](configuration.md).
- **No command methods**: configure trusted executable roots or shell profiles and inspect `available_methods`.
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
