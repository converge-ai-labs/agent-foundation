# Execution Isolation

## Design Position

Every `shell.exec` and `process.start` command is untrusted local code. By default, envd places each command tree inside a required operating-system isolation backend before the requested executable starts:

- Linux uses bubblewrap namespaces;
- macOS uses Seatbelt;
- Windows uses an AppContainer or equivalent restricted capability token plus capability-specific filesystem and network ACL projection, and a non-breakaway Job Object for complete process-tree ownership and cleanup.

Linux, macOS, and Windows are all required targets with platform-native containment and truthful cleanup semantics.

A deployment that already places envd inside a container, VM, remote sandbox, or equivalent boundary can explicitly configure `disabled`. That delegates child containment to the outer Host while preserving command validation, configured-mount authorization, transactional spawn, environment filtering, process ownership, output bounds, quotas, signaling, and cleanup.

There is no `auto`, `best_effort`, or probe-driven fallback. Required isolation either passes its production probe for the active platform and policy or envd fails before carrier admission. Every required-isolation preflight failure preserves its native cause and identifies explicit `disabled` configuration only as an option when a trusted outer sandbox owns command containment.

## Boundaries

| Concern                                                                                   | Owner                                                                          | Relationship                                             |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------- |
| Outer container, VM, remote sandbox, identity, and teardown                               | Host provider                                                                  | Can be the explicit containment owner in `disabled` mode |
| Required/disabled selection, runtime roots, network ceiling, and trusted payload identity | [Daemon Lifecycle and Configuration](01-daemon-lifecycle-and-configuration.md) | Immutable trusted bootstrap                              |
| Configured mount authority and file-operation policy                                      | [Resource Operations](04-resource-operations.md)                               | Supplies only operator-approved roots                    |
| Per-command policy, platform backend, probe, posture, and cleanup guarantee               | This document                                                                  | Establishes child containment before exec                |
| Command schema, transactional start, process state, and semantic controls                 | [Command and Process Execution](05-command-and-process-execution.md)           | Uses one backend-neutral execution object                |

Required isolation protects host resources outside configured command grants from payload code running under envd's ordinary OS authority. It is not a kernel-hard hostile multi-tenant boundary and does not claim resistance to kernel, bubblewrap, Seatbelt, AppContainer, or privileged Host compromise.

The isolation layer does not provision a container or VM, isolate envd file methods from envd itself, make Harness plugins untrusted, provide domain-name egress policy, or accept a profile, native root, helper, token, SID, entitlement, disable flag, or network widening from EIP.

## Configuration Contract

Isolation configuration is immutable trusted bootstrap owned by [Daemon Lifecycle and Configuration](01-daemon-lifecycle-and-configuration.md). It selects `required` or `disabled`, a daemon-wide `host` or `deny` network ceiling, reviewed extra read-only runtime roots, and an optional paired Linux payload UID/GID.

`required` is the default. A request can narrow configured `host` to `deny` only when the active backend reports per-command support. `disabled` accepts only outer-Host networking and cannot claim read-only projection for extra native roots. Unknown or inconsistent combinations fail startup. Platform detection, root/admin identity, CI, transport choice, and failed probing never select `disabled` automatically.

Linux payload IDs are trusted paired configuration. The unprivileged user namespace establishes final IDs, maps inherited supplementary groups to overflow IDs with no Host group authority, drops capabilities, prevents privilege regain, and applies no-new-privileges before exec. EIP request fields never select payload identity.

## Backend Contract

Before local readiness, envd selects one backend and proves its configured filesystem, network, initial-process status, and platform cleanup semantics. Linux and Windows establish complete tree ownership through their PID-namespace or Job boundary. macOS establishes inherited Seatbelt capability confinement plus the managed process-group cleanup defined below. Each command then receives an immutable policy derived from its authorized cwd/executable, configured read/write posture, reviewed runtime roots, private home/temp, protected roots, network narrowing, rebuilt environment, and finite limits.

The command manager interacts only through portable outcomes: requested-executable start, status, interrupt/terminate where advertised, force cleanup, and backend cleanup evidence. It never assumes a host PID is the requested executable or complete tree. A policy change cannot silently widen a live command, and selectors from another generation are never retargeted.

## Filesystem Authority

Required isolation starts deny-by-default and exposes only:

| Root class                                                                              | Payload access                                                                                                   |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Command `cwd` configured mount                                                          | Full selected mount, bounded by configured read/write and command policy                                         |
| Private execution home                                                                  | Read-write; distinct from host or daemon home                                                                    |
| Private execution temporary root                                                        | Read-write; forced through temporary-directory environment variables                                             |
| Curated platform runtime                                                                | Read and execute only as required for supported shells, loaders, tools, certificates, locale, and account lookup |
| Explicit extra root                                                                     | Read and execute only after trusted startup validation                                                           |
| Minimal devices and inherited stdio                                                     | Platform-specific minimum                                                                                        |
| Daemon config, carrier, runtime control, command output, helpers, logs, and credentials | Denied                                                                                                           |
| Host home, unrelated workspaces, sockets, credentials, and every other root             | Denied                                                                                                           |

An operator that intentionally needs whole-filesystem breadth configures an ordinary trusted mount such as `/` on POSIX or explicit volume roots on Windows. The session cannot switch envd into a special server-filesystem mode. Even a broad configured mount does not expose protected envd control paths to a required-isolation payload; startup and per-command policy must be able to subtract or deny them truthfully.

Runtime roots are reviewed paths, not ambient `PATH`. User-managed toolchains require explicit extra read-only roots. Required isolation does not expose SSH agents, container-engine sockets, pathname D-Bus endpoints, Keychain configuration, cloud credentials, daemon carrier state, or unrelated IPC merely for compatibility. On Linux, selecting `network="host"` deliberately retains the Host network namespace, including its abstract Unix-socket namespace; `network="deny"` creates a fresh network namespace and removes that authority. Pathname sockets remain absent unless an authorized projected root contains them.

### Protected paths and overlap

Protected paths include daemon configuration and secrets, attachment/bootstrap material, generation runtime control, command-output spool, staging ownership metadata, logs, installed helpers, execution policy data, and probe sentinels. Protected denial has precedence over configured grants.

Every configured root is canonicalized, checked for prohibited overlap, opened or otherwise fixed using the strongest platform capability available, and kept stable by the provider during bootstrap. Ancestor/descendant roots with conflicting policy are invalid. Exact generation-private execution-home and temporary children are deliberate exceptions to their protected parent.

A broad mount is accepted in required mode only when the active backend can enforce protected-path subtraction. If the production probe cannot prove that subtraction, startup fails; envd never exposes a protected subtree or silently changes to disabled execution.

The path checks establish configured authority, not portable compare-and-swap against another actor with native write access. Commands and external native writers can change authorized content concurrently. File mutation integrity remains limited to held objects and transfer evidence as defined by [Resource Operations](04-resource-operations.md).

## Child Environment and Launch Integrity

Envd separates launcher and payload environments:

- bubblewrap, `sandbox-exec`, Windows launch helpers, the supervisor, and policy constructors receive only a minimal daemon-owned environment;
- the typed command plan and final payload environment travel through a bounded private inherited channel;
- request values are installed only after the final sandbox/token, release gate, payload identity, and no-new-privileges posture are active;
- internal handles and descriptors have fixed purpose, bounded framing, and close-on-exec or non-inheritable behavior;
- every unrelated descriptor or Windows handle is closed before requested executable entry.

Arguments remain structured values. Canonical validated paths become typed backend parameters and are never interpolated into shell text, a Seatbelt profile, an ACL command line, or a helper script. A backend may retain native path handles when they are useful, but cross-platform command launch does not require descriptor-based executable dispatch or an immutable pathname snapshot. Helpers resolve from verified trusted locations, never the workspace or payload `PATH`.

The final payload environment is rebuilt by the command owner. `HOME` and temporary values point to private generation roots. Daemon attachment credentials, `A13N_ENVD_*` control values, runtime paths, dynamic-loader injection values, ambient service credentials, internal handle names, and carrier state are absent.

## Start Boundary

The requested executable cannot run until the selected containment policy is active and envd has committed command, process, and output ownership. Failure before that boundary starts no payload. Loss of evidence after possible payload release preserves conservative ownership and produces `unknown_outcome`; envd never retries through a weaker backend.

The exact gate, supervisor, launch channel, and acknowledgement frames are private backend mechanisms. They must preserve requested-executable status rather than substituting a wrapper's status, and every internal frame or handle remains bounded and absent from the payload.

## Linux Bubblewrap Backend

Linux required mode uses the verified OS-managed non-setuid helper at `/usr/bin/bwrap`. The Host kernel and active Linux Security Modules must permit the daemon user to create unprivileged user namespaces. Envd never searches `PATH`, uses a setuid helper, changes Host kernel or LSM policy, or falls back after Host policy denies user namespaces.

For each command the backend creates:

- fresh user and mount namespaces;
- an empty synthetic root populated only by approved projections;
- a PID namespace with private `/proc` and an envd-owned PID 1 supervisor;
- isolated IPC and UTS namespaces;
- minimal `/dev`;
- a new session, an anonymous session keyring, denied keyring-management syscalls, dropped capabilities, and no-new-privileges;
- a fresh network namespace with no host routes or abstract Host Unix sockets when effective policy is `deny`.

The supervisor reaps descendants, preserves initial-executable status, maps semantic signals, and destroys residual descendants. A descendant cannot evade cleanup by creating another process group or session inside the PID namespace. Force cleanup destroys the namespace from the outer manager.

Required Linux availability means the installed helper, linkage, kernel, LSM, unprivileged user namespaces, mount behavior, protected-path subtraction, PID lifecycle, and selected network posture passed the production probe.

## macOS Seatbelt Backend

macOS required mode uses `/usr/bin/sandbox-exec` with a generated `deny default` Seatbelt profile. Canonical paths are passed as profile parameters rather than interpolated into profile source. Descendants inherit the restriction.

The profile grants only reviewed process execution, selected configured/runtime roots, private home/temp, minimal devices, narrow platform IPC, and the effective IP-network posture. It denies unrelated paths, Unix sockets, Mach services, IOKit clients, preferences, and IPC by default. A reviewed versioned Mach-service allowlist avoids blanket Keychain or `securityd` access. The claim is limited to deny-by-default filesystem and reviewed service grants rather than absolute Keychain isolation across every macOS release.

Under `host`, required IP socket and DNS/trust operations are granted while filesystem policy remains. Under `deny`, IP networking grants are omitted while only narrow local platform IPC remains.

Seatbelt establishes inherited capability confinement, not an immutable process-ownership container. Envd manages the initial process group, which includes descendants that remain in that group. `cleanup="complete"` means that managed process group no longer exists and envd knows of no managed residual; it does not claim discovery of a descendant that deliberately creates another process group or session before observation. A residual inside the managed target yields `cleanup="residual_confined"` only while inherited Seatbelt confinement remains proven; loss of confinement evidence yields cleanup failure.

Bare macOS therefore does not provide Linux PID-namespace or Windows Job-style adversarial whole-tree ownership. A Host that requires strict teardown of deliberately detached descendants places envd inside a disposable VM, container, or equivalent outer Environment and destroys that boundary after envd shutdown. This outer lifecycle responsibility does not weaken the inner required Seatbelt filesystem, network, or IPC policy.

Seatbelt's public launch surface is deprecated. Each supported macOS release must pass the production probe. Removal or semantic breakage fails required startup; envd does not silently substitute an unreviewed sandbox.

## Windows AppContainer and Job Backend

Windows required mode combines two independent mechanisms:

1. **AppContainer or equivalent restricted capability token** establishes the payload security principal and default-deny capability boundary. Capability-specific filesystem and network grants define what that principal can access.
2. **Job Object ownership** establishes the complete process-tree lifecycle. Every payload process enters one non-breakaway job before it can execute; kill-on-job-close and active-process controls provide bounded whole-tree cleanup.

A Job Object alone is not a sandbox. It cannot replace the AppContainer/restricted-token and ACL projection that enforces filesystem and network containment. Conversely, an AppContainer token alone does not prove complete descendant cleanup.

### Capability and ACL projection

Each Windows command receives a restricted identity whose effective capabilities contain only its selected configured mount, reviewed runtime roots, private home/temp, and configured network posture. Envd applies filesystem grants through native security APIs without broadening Users, Everyone, the daemon user, or another live command. Concurrent commands with different policies cannot gain or revoke one another's authority.

Protected and unrelated paths, registry, named-pipe, COM, device, and network capabilities remain absent unless a narrow reviewed backend requirement is part of the production probe. `deny` grants no IP-network capability.

Any ACL/profile/projection state created for a command remains envd-owned until it is removed after process-tree completion. Cleanup changes only envd-owned grants and must not rewrite unrelated operator ACLs. If authority removal cannot be proven, cleanup fails and capacity remains charged. Exact SID allocation, projection layout, rollback records, and retry algorithms are private implementation choices.

### Job and process controls

The backend creates the Job Object before payload release, disables child breakaway, assigns the gated initial process before it can execute request code, and retains the job handle in the execution owner. Descendants inherit job membership. Completion-port or equivalent native notifications drive process accounting without polling an untrusted PID list.

`interrupt` and `terminate` are advertised only when the backend has distinct, tested semantic mechanisms for the selected process kind. Unsupported semantic signals return `unsupported`; they are never silently mapped to force kill. `process.kill` closes or terminates the owned job and waits for job-empty evidence. `cleanup="complete"` under this backend means the job reported no remaining members; its descriptor reports `cleanup_guarantee="job_complete"`.

The production probe proves token identity, filesystem read/write grants and denials, protected-path/reparse containment, network posture, inherited restriction, non-breakaway descendants, status preservation, semantic signals that are advertised, and job-wide cleanup. Merely creating an AppContainer profile or Job Object is insufficient.

## Disabled Mode

In `disabled` mode envd still validates configured mounts, cwd, typed executable, command schema, environment, limits, and process ownership. Those path checks are admission-time authority checks, not proof that the child cannot access other resources visible to its outer OS identity.

The payload can access every path, process, device, IPC endpoint, and network resource made visible by its outer container, VM, sandbox, or native account. The outer Host must also prevent payload access to envd memory, bootstrap credentials, service configuration, carrier state, command output, and control channels. Running untrusted payloads with the same unrestricted process-inspection, debugger, administrator/root, or control-channel authority as envd is not a valid outer boundary.

Envd uses the strongest ordinary native process target available. Windows commands enter a non-breakaway, kill-on-close Job before payload release; normal root exit still triggers descendant cleanup, and completion requires native Job-empty evidence. This native lifecycle ownership does not claim child filesystem or network containment. The descriptor reports backend `outer_host`, all containment booleans false, and cleanup guarantee `outer_host`. Complete outer teardown remains provider evidence, not an envd inference.

## Network Policy

| Effective policy | Linux required                              | macOS required            | Windows required                           | Disabled                   |
| ---------------- | ------------------------------------------- | ------------------------- | ------------------------------------------ | -------------------------- |
| `host`           | Host IP namespace remains visible           | Reviewed IP socket grants | Reviewed AppContainer network capabilities | Outer Host owns networking |
| `deny`           | Fresh network namespace with no host routes | IP socket grants omitted  | No IP-network capability                   | Unsupported                |

`host` means unrestricted IP networking visible from the outer Environment, including localhost, LAN, metadata endpoints, inbound binds, downloads, and exfiltration of readable data. It is compatibility, not egress filtering. Neither posture defines domain, URL, port-destination, proxy, or metadata-only rules.

## Production Probe and Readiness

Required mode runs the installed backend, policy builder, supervisor, payload launcher, and cleanup path before local readiness and before stdio initialization or reverse-WebSocket connection admission.

The bounded platform probe proves:

- helper/system API selection and integrity;
- configured mount read/write behavior and read-only denial;
- protected and random host sentinel denial, including subtraction from a broad configured mount;
- symlink or reparse-point containment;
- private home and temporary roots;
- child environment and inherited descriptor/handle hygiene;
- descendant restriction after fork/spawn and exec;
- requested-executable status preservation;
- advertised signal semantics;
- platform cleanup at the reported guarantee;
- configured and per-command network posture;
- platform-specific namespace, Seatbelt, AppContainer/ACL, and Job evidence.

A timeout, helper mismatch, unsupported kernel/OS/filesystem, LSM denial, profile failure, ACL restoration failure, network discrepancy, or cleanup discrepancy fails startup. Disabled mode runs no sandbox conformance probe, emits one structured startup warning, and reports false inner containment.

## Descriptor Posture

The serialized descriptor posture is:

```python
class IsolationPosture(BaseModel):
    mode: Literal["required", "disabled"]
    backend: Literal[
        "linux_bubblewrap",
        "macos_seatbelt",
        "windows_appcontainer",
        "outer_host",
    ]
    filesystem_containment: bool
    process_containment: bool
    network_containment: bool
    network_policy: Literal["host", "deny"]
    cleanup_guarantee: Literal[
        "namespace_complete",
        "residual_confined_possible",
        "job_complete",
        "outer_host",
    ]
```

True fields in required mode mean the production probe passed for this generation. On macOS, `process_containment=true` means descendants inherit Seatbelt capability restrictions; it does not claim Linux PID-namespace or Windows Job-style whole-tree ownership. `network_containment` is true only for daemon-wide `deny` and does not imply that a configured `host` posture can narrow one command. Exact optional request truth is separate in `EnvironmentDescriptor.execution_features`: process-count, memory-byte, CPU-time, per-command-deny, interrupt, and terminate booleans become true only after the active production path proves those semantics. `process.signal` is available exactly when one advertised signal action is true; other command methods remain independently available when optional limits are false.

The descriptor reveals no helper path, profile source, SID, ACL, protected root, sentinel, host identity, or outer-provider claim.

## Failure Semantics

| Failure                                                                      | Outcome                                                            |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Invalid isolation/network/path/identity configuration                        | Startup fails before readiness                                     |
| Required backend unsupported or production probe fails                       | Startup fails; no fallback                                         |
| Per-command policy, token, namespace, profile, ACL, or root projection fails | `execution_isolation_failed` before payload release                |
| Supervisor is not ready under final policy                                   | Prepared tree is cleaned; no payload starts                        |
| Owner-store commit fails                                                     | Gate remains closed; prepared tree is cleaned                      |
| Gate, identity, token, or no-new-privileges setup fails                      | Public start rolls back with `execution_isolation_failed`          |
| Requested executable cannot execute                                          | Public start rolls back with `command_start_failed`                |
| Child access is denied                                                       | Ordinary child stderr/exit behavior; never unsandboxed retry       |
| Backend evidence is lost after exec                                          | Strongest cleanup plus `backend_lost` and unknown-outcome evidence |
| Linux namespace cleanup fails                                                | `cleanup_failed`; completeness not claimed                         |
| macOS managed process-group exit remains unproven but confinement holds      | `residual_confined`                                                |
| Windows Job Object cannot prove empty or ACL cleanup is uncertain            | `cleanup_failed`; job/authority cleanup not claimed                |
| Disabled native target cannot prove cleanup                                  | `cleanup_failed`; never `residual_confined`                        |

Diagnostics identify platform and bounded failure class but exclude command text, environment, credential, profile source, SID, ACL contents, helper path, denied path, file content, and sensitive host structure.

## Compatibility and Conformance

A backend is compatible only when it preserves required/disabled fail-closed selection, configured-mount and protected-path semantics, transactional no-unregistered-execution, requested-executable acknowledgement and status, semantic process controls, cleanup outcomes, child-environment hygiene, descriptor posture, and production probing.

Changing backend internals is compatible behind those facts. Weakening deny-by-default roots, silently falling back, reporting complete cleanup without evidence, mapping unsupported signals to kill, treating a Job Object as filesystem containment, or making profiles/capabilities request-selectable is incompatible.

Conformance includes native tests on every published OS/architecture, failure injection around transactional boundaries, descendant inheritance and cleanup, root overlap and link/reparse behavior, credential/handle inheritance, host/deny networking, common toolchain compatibility, and disabled posture. Cross-compilation does not substitute for native Windows, macOS, or Linux runtime evidence.

## Trade-offs

### Native isolation across three operating systems

Three native backends increase implementation and release-matrix cost. They preserve local toolchains and low startup overhead while giving direct-machine deployments a real fail-closed boundary. Providers that require hostile multi-tenancy still use an outer VM or equivalent sandbox.

### Windows capability projection plus Job ownership

AppContainer/ACL projection adds lifecycle-sensitive ACL work, and Job Objects add a separate process owner. Keeping both is necessary: capability projection owns resource containment; the job owns descendants and cleanup. Collapsing them would overstate one mechanism's guarantees.

### Fail closed instead of compatibility fallback

Required mode can make envd unavailable when user namespaces, Seatbelt, AppContainer, ACL projection, or Job semantics are unavailable. Explicit `disabled` lets a trusted outer provider own containment without silently turning a platform regression into unrestricted execution.

## Invariants

01. Every command uses one immutable isolation policy snapshot before transactional preparation.
02. Required isolation is the default on Linux, macOS, and Windows, probes before carrier admission, and never falls back.
03. Disabled mode is explicit and delegates only child containment; all other envd controls remain active.
04. EIP callers cannot select isolation mode, backend, helper, profile, AppContainer identity, ACL, native root, payload identity, or wider network policy.
05. Required filesystem authority contains only the configured command mount, private execution roots, curated runtime, explicit read-only roots, minimal devices, and stdio, with protected-path denial taking precedence.
06. Request values reach only the final payload after isolation and identity setup; daemon credentials and carrier state never reach helpers or payloads.
07. No requested executable runs before final backend readiness and process/output ownership commit.
08. Linux uses verified non-setuid bubblewrap with mount/user/PID/IPC/UTS isolation, private `/proc`, PID 1 supervision, and optional network namespace.
09. macOS uses parameterized deny-default Seatbelt and reports its no-PID-namespace cleanup limitation honestly.
10. Windows required mode combines AppContainer/restricted capabilities and capability-specific ACL/network projection for containment with a non-breakaway Job Object for complete process-tree ownership.
11. A Windows Job Object alone is never reported as filesystem or network isolation, and unsupported signal semantics are never mapped silently to kill.
12. Network `host` is unrestricted outer-Environment IP compatibility; `deny` removes IP authority through the active backend; neither is domain policy.
13. Only backend evidence can report complete cleanup, and only macOS required isolation can report a residual as still confined without proving exit.
14. Envd posture reports only its own inner layer and never infers outer container, VM, or provider strength.
