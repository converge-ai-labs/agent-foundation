# Isolation and troubleshooting

Run the production probe with the same executable, configuration, runtime identity, and ordinary operating-system account as the daemon. A successful build, HTTP connection, or protocol test does not establish a working isolation boundary.

## Isolation behavior

Execution isolation defaults to `required` and fails closed when its native backend is unavailable.

| Platform | Required backend                                   | Current behavior                                               |
| -------- | -------------------------------------------------- | -------------------------------------------------------------- |
| Linux    | `/usr/bin/bwrap` with unprivileged user namespaces | Supported; complete posture is probed before carrier admission |
| macOS    | `/usr/bin/sandbox-exec` using Seatbelt profiles    | Supported                                                      |
| Windows  | Native backend not yet delivered                   | `required` fails closed                                        |

Run the production isolation preflight with the same execution configuration used for launch. Supply `--config /absolute/path/to/a13n-envd.json` when the policy comes from JSON:

```bash
env -u A13N_ENVD_EXECUTABLE a13n-envd isolation probe --json
```

The POSIX command removes the Python Host's executable-selector variable, which is not daemon bootstrap. The probe checks execution posture, not complete mount, runtime, or carrier readiness, and can run bounded local subprocesses. See [probe behavior](configuration.md#check-before-admitting-work).

Set `A13N_ENVD_EXECUTION_ISOLATION=disabled` only when a trusted outer container, VM, or equivalent boundary owns command containment. Disabled mode is explicit, reports containment features as unavailable, and emits a startup warning. It is not a fallback for a failed required-isolation probe.

On Linux, install the distribution's non-setuid Bubblewrap package at `/usr/bin/bwrap`. The kernel and active Linux Security Modules must allow the daemon user to create unprivileged user namespaces. `a13n-envd` does not change sysctls, load AppArmor policy, search `PATH` for the helper, or fall back to disabled mode.

### Windows: client support versus execution isolation

EIP is platform-neutral. A Windows client can connect to a Linux or macOS daemon and use the capabilities that daemon actually advertises. It does not make the remote execution host Windows, nor add client-side isolation. Harness UI offers Full Control for built-in local execution on Windows and rejects an explicit Sandbox selection without acquiring a daemon or falling back. Until the native required backend is available, Windows daemon file and command operations below require an explicitly configured, trusted outer sandbox with `disabled`; the default startup does not admit even file-only sessions.

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
sysctl kernel.apparmor_restrict_unprivileged_userns \
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
bwrap --unshare-user --unshare-pid \
  --ro-bind / / --proc /proc --dev /dev /bin/true
env -u A13N_ENVD_EXECUTABLE a13n-envd isolation probe --json
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

For an externally operated Envd deployment inside a container, the operator may explicitly select disabled isolation when the outer container owns containment. The native Docker Environment Provider does not use Envd. Do not use that setting as a workaround on an otherwise unsandboxed bare host.

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
- [Environment Provider guide](../environments/index.md)
- [`a13n-envd` crate README](https://github.com/converge-ai-labs/agent-foundation/tree/main/crates/a13n-envd)
- [`a13n-envd-client` README](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-envd-client)
- [Normative a13n-envd and EIP specifications](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/a13n-envd)
