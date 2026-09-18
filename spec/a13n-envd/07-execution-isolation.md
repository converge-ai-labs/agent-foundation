# Execution Boundary and Native Cleanup

## Design Position

The Host supplies the security boundary for envd and its children before launching the daemon. This can be a POSIX UID, an OS sandbox, Docker, a VM or a remote Provider's sandbox. Envd does not construct a separate security boundary for each command or validate a startup filesystem ceiling.

A trusted Host may select any folder accessible to that deployment. The Session working directory supplies defaults, not containment. File operations and commands may access other paths permitted by the daemon account and outer boundary. Host file-operation ceilings do not make shell execution read-only.

## Responsibilities

| Concern                                                                     | Owner                          |
| --------------------------------------------------------------------------- | ------------------------------ |
| OS identity, filesystem/network restrictions and disposable target teardown | Host launcher or Provider      |
| Device authentication and product-user permission to select it              | Host and transport integration |
| Session routing, native operation correctness and resource accounting       | Envd                           |
| Command start, stdin, status, signals, bounded output and cleanup           | Envd                           |
| Model tool/action selection and whether shell execution is offered          | Host and Harness               |

One daemon per machine is the normal arrangement, not a promise that one OS account safely serves mutually hostile tenants. A different security boundary requires a different daemon deployment. Envd does not accept payload UID/GID, sandbox helpers, filesystem policy or per-command network policy from EIP.

## Host Launch Modes

A Full Control deployment deliberately runs with its selected account's access. A Sandbox product must launch the entire daemon inside the promised outer boundary, so file RPCs and child processes share that boundary. It cannot label a plain account launch as sandboxed merely because selected folders or a working directory were provided.

An unavailable requested sandbox fails Host preparation; it does not silently fall back to Full Control. The Host owns launch-policy inspection and validation. EIP reports supported operations and cleanup features, not a claim that envd has proved an externally configured sandbox.

A shared Host-owned daemon may serve multiple Sessions only within the same launch boundary. Adding an inaccessible folder fails Session preparation; widening an outer sandbox requires a separately prepared launch, not EIP permission changes. Native containers and E2B retain their own lifecycle ownership.

## Child Environment and Runtime Data

Commands receive the configured ordinary execution environment plus explicit request changes. Transport credentials and daemon control values are not copied into child environment variables or inherited control descriptors. This prevents accidental disclosure, not hostile same-account access. Envd does not deny access to a hidden inventory of paths or sanitize a general programming environment into a security sandbox.

Daemon-owned runtime data is managed separately from workspace data and uses appropriate native permissions. Those permissions and the outer deployment determine who can access it. Do not claim protection from arbitrary code sharing the same native account where the OS provides none.

## Native Cleanup

Envd registers a native process target before payload release, drains stdout/stderr and requests cleanup on explicit kill, Session close/expiry, output overflow or shutdown. Initial executable exit and descendant cleanup are separate observations.

POSIX process-group cleanup and Windows Job-based ownership can be used where supported. A POSIX process group is not a security sandbox and cannot guarantee teardown of adversarial descendants that leave it. A Windows Job establishes process ownership, not filesystem or network isolation. A Host requiring strict cleanup of arbitrary descendants uses an outer target it can destroy.

Status reports only observed cleanup: pending, complete for the advertised managed target, or failed. It never substitutes helper exit for payload exit or claims complete machine-wide descendant termination without such ownership. Uncertain cleanup remains accounted for and is surfaced to the Host.

## Resource Limits

Generous Session and daemon limits bound managed command count, output, transfers and bookkeeping. They are operational bounds, not a hostile-process resource controller. Host cgroups, containers, job restrictions or equivalent controls supply hard whole-deployment CPU/memory/process limits when needed.

Optional per-command native limits and signals are advertised only when enforceable. Unsupported requested limits fail before execution rather than being silently ignored. Wall-time cancellation and bounded captured output remain envd responsibilities regardless of outer isolation.
