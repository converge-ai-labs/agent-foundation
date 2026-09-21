# Execution Boundary and Native Cleanup

## Design Position

The Host supplies the security boundary for envd and its children before launching the daemon. This can be a POSIX UID, an OS sandbox, Docker, a VM or a remote Provider's sandbox. Ordinary Sessions use that boundary directly. Opt-in controlled Sessions add the egress boundary defined below.

A trusted Host may select any folder accessible to that deployment. For ordinary Sessions, the working directory supplies defaults, not containment. File operations and commands may access other paths permitted by the daemon account and outer boundary. Host file-operation ceilings do not make shell execution read-only.

## Responsibilities

| Concern                                                                     | Owner                          |
| --------------------------------------------------------------------------- | ------------------------------ |
| OS identity, filesystem/network restrictions and disposable target teardown | Host launcher or Provider      |
| Device authentication and product-user permission to select it              | Host and transport integration |
| Session routing, native operation correctness and resource accounting       | Envd                           |
| Command start, stdin, status, signals, bounded output and cleanup           | Envd                           |
| Model tool/action selection and whether shell execution is offered          | Host and Harness               |

One daemon per machine is the normal arrangement, not a promise that one OS account safely serves mutually hostile tenants. A mutually hostile tenant boundary requires a separately prepared deployment. EIP does not accept arbitrary payload UID/GID, sandbox helpers or per-command filesystem policy.

## Host Launch Modes

A Full Control deployment deliberately runs with its selected account's access. A Sandbox product must launch the entire daemon inside the promised outer boundary, so file RPCs and child processes share that boundary. It cannot label a plain account launch as sandboxed merely because selected folders or a working directory were provided.

An unavailable requested sandbox fails Host preparation; it does not silently fall back to Full Control. The Host owns launch-policy inspection and validation. EIP reports supported operations and cleanup features, not a claim that envd has proved an externally configured sandbox.

A shared Host-owned daemon may serve multiple Sessions only within the same launch boundary. Adding an inaccessible folder fails Session preparation; widening an outer sandbox requires a separately prepared launch, not EIP permission changes. Native containers and E2B retain their own lifecycle ownership.

## Child Environment and Runtime Data

Ordinary commands receive the configured execution environment plus explicit request changes. Transport credentials and daemon control values are not copied into child environment variables or inherited control descriptors. This prevents accidental disclosure, not hostile same-account access. Ordinary Sessions do not hide filesystem paths; controlled Sessions protect the broker paths described below.

Daemon-owned runtime data is managed separately from workspace data and uses appropriate native permissions. Those permissions and the outer deployment determine who can access it. Do not claim protection from arbitrary code sharing the same native account where the OS provides none.

## Native Cleanup

Envd registers a native process target before payload release, drains stdout/stderr and requests cleanup on explicit kill, Session close/expiry, output overflow or shutdown. Initial executable exit and descendant cleanup are separate observations.

POSIX process-group cleanup and Windows Job-based ownership can be used where supported. A POSIX process group is not a security sandbox and cannot guarantee teardown of adversarial descendants that leave it. A Windows Job establishes process ownership, not filesystem or network isolation. A Host requiring strict cleanup of arbitrary descendants uses an outer target it can destroy.

Status reports only observed cleanup: pending, complete for the advertised managed target, or failed. It never substitutes helper exit for payload exit or claims complete machine-wide descendant termination without such ownership. Uncertain cleanup remains accounted for and is surfaced to the Host.

## Resource Limits

Generous Session and daemon limits bound managed command count, output, transfers and bookkeeping. They are operational bounds, not a hostile-process resource controller. Host cgroups, containers, job restrictions or equivalent controls supply hard whole-deployment CPU/memory/process limits when needed.

Optional per-command native limits and signals are advertised only when enforceable. Unsupported requested limits fail before execution rather than being silently ignored. Wall-time cancellation and bounded captured output remain envd responsibilities regardless of outer isolation.

## Controlled Session Egress

A daemon JSON `egress.enabled: true` permits `session.open` to include an egress policy. Presence selects a controlled Session; absence preserves ordinary behavior. Unsupported isolation rejects creation before any payload executes, without fallback. The Host retains outer sandbox, CPU/memory and product authorization ownership. Egress does not isolate mutually hostile trusted Host callers or privileged operators sharing the Device.

The broker owns real secret values, policy revisions, upstream connections and the Session CA private key. A separate worker owns the complete Session filesystem/execution engine, private process/network/mount namespaces, and no real injected credentials. Kernel forwarding is independent of proxy environment variables. The outer Session alone owns idle/grace expiry and carrier attachment. Session close, expiry or broker loss fences networking and confirms worker death before releasing active resource reservations.

The worker's workspace and private temporary/runtime storage are writable. The remaining image is read-only; envd configuration, transport credential/key files, broker runtime and the launching account's `.a13n` directory are hidden. Protected files with extra hardlinks, or overlapping workspaces, reject preparation. Unrelated image/workspace data remains the Host's responsibility. Controlled payloads have no capabilities or privilege gains, cannot create raw/packet/VSOCK/netlink or named Unix sockets, and cannot use io_uring to bypass socket restrictions. Anonymous Unix STREAM and SEQPACKET pairs remain supported; DGRAM pairs are denied. The implementation requires Linux 6.1.2 or newer, including the SEQPACKET reconnect-race fix.

`allow_hosts` absence allows public DNS names; an empty list denies external access; a nonempty list allows exact DNS names and explicitly listed public IPs. Wildcards are invalid. Direct IP access requires an explicit entry and never grants injection. Resolution rejects any nonpublic answer and pins the validated address for that connection. Local Session loopback is separate from the broker's loopback. The current payload interception path is IPv4; upstream DNS connections can use either address family. Native payload IPv6 has no external route.

A secret binding has `env`, write-only `value`, and nonempty exact `inject_hosts`. Injection hosts must be permitted by a restricted allowlist; without an allowlist they still constrain injection. The returned status contains revision, allowlist and binding metadata with `sentinel`, never values. Sentinels use `a13n_<8 random hex digits>_<ENV>` with Session-local collision checks. They avoid accidental replacement and do not authenticate callers. Values are absent from daemon configuration, command arguments, worker bootstrap, descriptors and ordinary logs.

Only HTTPS port 443 request header values substitute sentinels. URL and request-body bytes are unchanged. The broker validates upstream TLS and provides the Session CA trust material to ordinary clients. Exact real values are redacted from response headers, streaming bodies and trailers, including chunk boundaries. Transformed/encoded disclosures by an upstream are outside literal redaction. HTTP/1.1 is supported; upgrades and nonidentity response compression are rejected. HTTP port 80 denies sentinel-bearing headers without injection. Other TCP and UDP traffic follows destination policy without credential substitution.

`egress.update` is a Session-scoped, ledger-external compare-and-swap operation. `expected_revision` must match; validation and publication are atomic; success increments revision. `allow_hosts` omission preserves the policy, `[]` denies external access, and `unrestricted: true` restores public-domain access. `unrestricted` and `allow_hosts` together are invalid. `set_secrets` adds/replaces; `remove_secrets` deletes; overlapping changes for one variable are invalid. Scope removal closes affected existing connections. In-flight responses retain the secret snapshot needed to redact their own request's credentials.

Rotation preserves a binding's sentinel; delete/re-add allocates a new one and rejects retired markers in intercepted headers. New commands receive the current sentinel environment. Running process environments do not mutate. Bound secret and CA variables cannot be overridden/unset by command configuration. Policy metadata does not change the original operation retry fingerprint. Describe/attach expose current nonsecret policy metadata. Limits bound bindings (64), host lists (256), secret values (8192 bytes), DNS mappings (4096), TCP connections (128), UDP flows (128), and retired sentinels (4096).

Controlled Sessions reserve complete per-Session operation/process/transfer/spool/staging budgets against Device totals before preparation. This reduces simultaneous idle Session capacity compared with demand allocation, while preserving aggregate limits without a separate cross-process quota protocol. Worker death releases transient reservations. Unconfirmed persistent staging cleanup retains its charge; wrapper exit or RPC disconnection alone is not cleanup proof.
