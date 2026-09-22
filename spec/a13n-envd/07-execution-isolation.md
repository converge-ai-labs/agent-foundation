# Execution Boundary and Native Cleanup

## Design Position

The Host supplies the security boundary for envd and its children before launching the daemon. This can be a POSIX UID, an OS sandbox, Docker, a VM or a remote Provider's sandbox. Ordinary Sessions use that boundary directly. Opt-in controlled Sessions add the egress boundary defined below.

A trusted Host may select any folder accessible to that deployment. For ordinary Sessions, the working directory supplies defaults, not containment. File operations and commands may access other paths permitted by the [execution account and native sudo policy](01-daemon-lifecycle-and-configuration.md#execution-identity-and-native-sudo) and outer boundary. Host file-operation ceilings do not make shell execution read-only.

## Responsibilities

| Concern                                                                                    | Owner                          |
| ------------------------------------------------------------------------------------------ | ------------------------------ |
| Account provisioning, outer filesystem/network restrictions and disposable target teardown | Host launcher or Provider      |
| Applying trusted native execution identity and privilege-gain settings                     | Envd                           |
| Device authentication and product-user permission to select it                             | Host and transport integration |
| Session routing, native operation correctness and resource accounting                      | Envd                           |
| Command start, stdin, status, signals, bounded output and cleanup                          | Envd                           |
| Model tool/action selection and whether shell execution is offered                         | Host and Harness               |

One daemon per machine is the normal arrangement, not a promise that one OS account safely serves mutually hostile tenants. A mutually hostile tenant boundary requires a separately prepared deployment. EIP does not accept arbitrary payload UID/GID, sandbox helpers or per-command filesystem policy.

## Host Launch Modes

A Full Control deployment deliberately uses its selected execution account's access, with native sudo when permitted by startup configuration, sudoers and the outer platform. A Sandbox product must launch the entire daemon inside the promised outer boundary, so file RPCs and child processes share that boundary. It cannot label a plain account launch as sandboxed merely because selected folders or a working directory were provided.

An unavailable requested sandbox fails Host preparation; it does not silently fall back to Full Control. The Host owns launch-policy inspection and validation. EIP reports supported operations and cleanup features, not a claim that envd has proved an externally configured sandbox.

A shared Host-owned daemon may serve multiple Sessions only within the same launch boundary. Adding an inaccessible folder fails Session preparation; widening an outer sandbox requires a separately prepared launch, not EIP permission changes. Native containers and E2B retain their own lifecycle ownership.

## Child Environment and Runtime Data

Ordinary commands receive the configured execution environment plus explicit request changes. Transport credentials and daemon control values are not copied into child environment variables or inherited control descriptors. This prevents accidental disclosure, not hostile same-account access. Ordinary Sessions do not hide filesystem paths; controlled Sessions protect the broker paths described below.

Daemon-owned runtime data is managed separately from workspace data and uses appropriate native permissions. Those permissions and the outer deployment determine who can access it. Do not claim protection from arbitrary code sharing the same native account where the OS provides none.

## Native Cleanup

Envd registers a native process target before payload release, drains stdout/stderr and requests cleanup on explicit kill, Session close/expiry, output overflow or shutdown. Initial executable exit and descendant cleanup are separate observations.

POSIX process-group cleanup and Windows Job-based ownership can be used where supported. Linux supervisors retain only the signaling authority needed to terminate their managed group after sudo changes a descendant's UID, and reap adopted group children; payloads do not inherit that supervisor capability. A POSIX process group is not a security sandbox and cannot guarantee teardown of adversarial descendants that leave it. A Windows Job establishes process ownership, not filesystem or network isolation. A Host requiring strict cleanup of arbitrary descendants uses an outer target it can destroy.

Status reports only observed cleanup: pending, complete for the advertised managed target, or failed. It never substitutes helper exit for payload exit or claims complete machine-wide descendant termination without such ownership. Uncertain cleanup remains accounted for and is surfaced to the Host.

## Resource Limits

Generous Session and daemon limits bound managed command count, output, transfers and bookkeeping. They are operational bounds, not a hostile-process resource controller. Host cgroups, containers, job restrictions or equivalent controls supply hard whole-deployment CPU/memory/process limits when needed.

Optional per-command native limits and signals are advertised only when enforceable. Unsupported requested limits fail before execution rather than being silently ignored. Wall-time cancellation and bounded captured output remain envd responsibilities regardless of outer isolation.

## Controlled Session Egress

Trusted startup `egress.enabled: true` permits `session.open` to include an egress policy. Presence selects destination enforcement and credential injection; absence does not create a network policy. A controlled-capable daemon protects its management runtime from every Session, including those without an egress policy. Unsupported preparation fails before payload execution, without fallback. The Host retains outer sandbox, CPU/memory and product authorization ownership. Egress does not isolate mutually hostile trusted Host callers or privileged operators sharing the Device.

The broker owns real secret values, policy revisions, upstream connections and the Session CA private key. A separate worker owns the complete Session filesystem/execution engine, private process/mount namespaces, and no real injected credentials. Only Sessions with an egress policy receive a private network namespace and destination enforcement. Kernel forwarding is independent of proxy environment variables. The outer Session alone owns idle/grace expiry and carrier attachment. Session close, expiry or broker loss fences networking and confirms worker death before releasing active resource reservations.

Workers use the original writable sandbox system tree, not an overlay or a separate payload image. Native package installs, service accounts, permissions, ACLs, linker/CA updates and system changes persist across Sessions and are visible to the outer sandbox. Temporary/runtime, device and kernel views are private where needed for the broker boundary. Session-local Unix sockets, socketpairs, PTYs and ordinary TCP/UDP applications remain usable. Native sudo and file capabilities follow the trusted startup setting and native OS authorization, rather than a command allowlist.

The root-launched management runtime is a private immutable snapshot of broker/bootstrap executables, dependencies, resolver/account configuration, trust material and transport credentials, prepared before any Session. Management does not load code from the writable original tree after preparation. Every worker discards access to management mounts and outer process namespaces before loading original-tree NSS or executing payloads. Configuration, transport credential/key files, broker runtime and the launching account's `.a13n` directory are masked on the original-tree template before cloning worker views; renaming an ancestor does not reveal those files to later Sessions. Protected files with extra hardlinks, or overlapping workspaces, reject preparation. Unrelated credentials remain the Host's responsibility.

Restrictions prevent namespace/mount changes, broker/process introspection, raw device/kernel access and network-interception reconfiguration. They survive native sudo without suppressing ordinary filesystem/identity administration capabilities. Raw/packet/VSOCK sockets and io_uring socket bypasses are unavailable; Unix and ordinary netlink sockets are supported, while network-administration capabilities are not. Read-only kernel controls and safe device views do not make the original system tree read-only. The implementation requires a root launcher with native mount/PID/IPC/network namespace permissions, Linux 6.1.2 or newer (including the SEQPACKET reconnect-race fix), seccomp and pidfds. It does not rely on a single-UID user namespace.

The deployment must not contain foreign privileged services that execute payload-writable system files, cron jobs, or accessible privileged/socket network proxies outside the worker boundary. Such a service can bypass any per-process namespace by acting on shared files or requests. The launcher supplies trusted artifacts for every daemon start; a tree modified by prior root payloads is not a trusted source for the next broker bootstrap. These are deployment prerequisites, not properties envd can infer by scanning a shared machine. Controlled egress is not whole-machine flow isolation or isolation between mutually hostile Sessions sharing mutable system files.

`allow_hosts` absence allows public DNS names; an empty list denies external access; a nonempty list allows exact DNS names and explicitly listed public IPs. Wildcards are invalid. Direct IP access requires an explicit entry and never grants injection. Resolution rejects any nonpublic answer and pins the validated address for that connection. Local Session loopback is separate from the broker's loopback. The current payload interception path is IPv4; upstream DNS connections can use either address family. Native payload IPv6 has no external route.

A secret binding has `env`, write-only `value`, and nonempty exact `inject_hosts`. Injection hosts must be permitted by a restricted allowlist; without an allowlist they still constrain injection. The returned status contains revision, allowlist and binding metadata with `sentinel`, never values. Sentinels use `a13n_<8 random hex digits>_<ENV>` with Session-local collision checks. They avoid accidental replacement and do not authenticate callers. Values are absent from daemon configuration, command arguments, worker bootstrap, descriptors and ordinary logs.

Only HTTPS port 443 request header values substitute sentinels. URL and request-body bytes are unchanged. The broker validates upstream TLS and provides Session CA trust through a private bundle and bound command environment variables. It does not replace the native system CA bundle or edit sudoers. Native sudo may strip those variables; root HTTPS clients preserve them explicitly according to sudoers or configure their own trust store. Exact real values are redacted from response headers, streaming bodies and trailers, including chunk boundaries. Transformed/encoded disclosures by an upstream are outside literal redaction. HTTP/1.1 is supported; upgrades and nonidentity response compression are rejected. HTTP port 80 denies sentinel-bearing headers without injection. Other TCP and UDP traffic follows destination policy without credential substitution.

`egress.update` is a Session-scoped, ledger-external compare-and-swap operation. `expected_revision` must match; validation and publication are atomic; success increments revision. `allow_hosts` omission preserves the policy, `[]` denies external access, and `unrestricted: true` restores public-domain access. `unrestricted` and `allow_hosts` together are invalid. `set_secrets` adds/replaces; `remove_secrets` deletes; overlapping changes for one variable are invalid. Scope removal closes affected existing connections. In-flight responses retain the secret snapshot needed to redact their own request's credentials.

Rotation preserves a binding's sentinel; delete/re-add allocates a new one and rejects retired markers in intercepted headers. New commands receive the current sentinel environment. Running process environments do not mutate. Bound secret and CA variables cannot be overridden/unset by command configuration. Policy metadata does not change the original operation retry fingerprint. Describe/attach expose current nonsecret policy metadata. Limits bound bindings (64), host lists (256), secret values (8192 bytes), DNS mappings (4096), TCP connections (128), UDP flows (128), and retired sentinels (4096).

Controlled Sessions reserve complete per-Session operation/process/transfer/spool/staging budgets against Device totals before preparation. This reduces simultaneous idle Session capacity compared with demand allocation, while preserving aggregate limits without a separate cross-process quota protocol. Worker death releases transient reservations. Unconfirmed persistent staging cleanup retains its charge; wrapper exit or RPC disconnection alone is not cleanup proof.
