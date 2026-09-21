# Daemon Lifecycle and Configuration

## Design Position

One envd daemon normally serves one machine or outer sandbox, with many Host-created Sessions. The authenticated Host is trusted to choose any folder the daemon account can access. Envd does not maintain a startup filesystem allowlist, protected-root subtraction engine, per-Session sandbox or payload identity policy.

The Host chooses the POSIX UID, sandbox, Docker container, VM or equivalent boundary before launching envd. Different security boundaries require separate launches. [Execution Boundary](07-execution-isolation.md) defines launch isolation.

## Device Identity and Generation

`device_id` identifies the daemon installation, not a folder, Environment definition, Session or Run. An operator can provide it explicitly; otherwise envd creates and persists one in its installation state directory. Display name and description are optional metadata, never routing authority. One active reverse connection represents this Device at its Host.

Each process start creates a fresh nonzero unsigned 64-bit `generation`. Reconnection retains it; restart changes it. This is an equality fence, not a durable recovery mechanism. Session, process, output, operation and transfer selectors are invalid after restart. Native workspace files are unaffected.

A Host binds its registered Device to an endpoint or authenticated attachment and expected identity. First-contact discovery is a trusted registration action. An ID or display name alone does not authenticate a daemon. Device identity is independent of working directory. Clients discover or select a path before opening a Session.

## Trusted Configuration

Bootstrap contains only what the daemon needs to run:

- installation identity and optional display metadata;
- installation-state and disposable-runtime directories;
- stdio, HTTP or reverse-WebSocket transport settings and credentials;
- `default_working_directory` and `directory_discovery` (default `true`);
- explicit Full Control or shell profiles and executable search configuration;
- generous finite per-Session and aggregate resource limits;
- inactivity, short disconnect grace, completed-history retention and collection settings.

An omitted default working directory uses the daemon's startup cwd. An explicit value is an absolute native path. Envd resolves it to an absolute Device path and returns it in Device info. The default is a working starting point, not an access boundary. No discovery-entry list or filesystem allowlist is configured. Unavailable defaults remain visible but fail Session opening; they do not prevent clients from selecting another path.

`full_control: true`, or `A13N_ENVD_FULL_CONTROL=1` when the file does not select it, enables native command execution without shell-profile declarations. It selects a platform shell and preserves inherited `PATH` order and command environment, excluding daemon bootstrap variables. Full Control uses the daemon account's authority, not a daemon-owned sandbox. Explicit shell profiles and executable roots are an alternative to Full Control, not an additional policy layered over it. The flag does not alter an outer container, account or sandbox.

Clients may override the default at `session.open`. Existing Sessions retain their resolved working directory. Disabling directory discovery removes only Device-level enumeration; known paths and ordinary Session file access remain usable. No EIP method changes launch identity or networking.

An explicit configuration file, non-secret CLI inputs and documented environment fallbacks supply bootstrap. Explicit values take precedence over fallbacks. Configuration requires valid types, required transport fields and positive finite bounds. Transport credentials stay out of command arguments, URLs, logs and child environment values. Native permissions protect configuration and runtime files within the chosen deployment boundary; envd does not claim to hide them from arbitrary code running as the same account.

`DaemonLimits` bounds concurrent Sessions, operations, commands, transfers, output records/bytes and staging bytes. Per-Session limits prevent one owner from accidentally consuming all capacity, while aggregate limits apply across every Session. Protocol messages, queues and individual transfers remain bounded. Capacity is reserved before allocation or native dispatch and released only after actual cleanup. Exhaustion triggers collection of eligible resources before returning `busy` or `quota_exceeded`; it does not terminate healthy sibling work.

Client-actionable bounds and lifecycle timings appear in the descriptor. Specific default sizes and collection intervals are implementation tuning, not additional wire protocols. [Resource Lifetime](09-resource-lifetime-and-reclamation.md) owns eligibility and cleanup semantics.

## Generation-Private Runtime State

Runtime state contains Session bookkeeping, spool files and daemon-owned temporary data, separate from workspace files and persistent installation identity. A lifetime lock prevents two daemon processes from using the same runtime directory. On startup, remove only recognized daemon-owned stale generation data without following links into user data; unexpected content or failed deletion is reported rather than recursively treating an arbitrary directory as disposable.

Each generation gets fresh runtime storage. Nothing in it is a recovery checkpoint. Destination-local upload candidates remain beside their destination for atomic publication and are tracked by their owning Session/operation. The daemon never garbage-collects ordinary workspace files.

## Startup and Readiness

Startup loads configuration, establishes identity/generation and runtime ownership, initializes the Session registry and aggregate accounting, then admits its configured transport. It does not probe or construct a command sandbox.

Three observations are distinct:

1. Local readiness: the daemon's own resources and configured transport can run.
2. Device readiness: an authenticated/private handshake verifies identity, generation and protocol.
3. Session readiness: the selected working directory is usable and an `environment.readiness` round trip verifies the new operation scope.

A Device can stay connected with zero Sessions. Failure to open or prepare one Session leaves siblings usable. Readiness is not a reservation for future work or proof that an external service is healthy.

## Runtime Ownership

The daemon owns its Session registry, transport and aggregate accounting. Each Session owns its fixed default working directory, operation admission/evidence, processes, output and transfers. Handlers receive the selected Session explicitly; there is no mutable global current workspace.

A carrier owns delivery, not native resource identity. An accepted file commit retains its frozen destination and candidate until completion or reported failure even if Session cleanup begins. Closing cannot turn possible publication into a claim of rollback. Cleanup waits or reconciles accepted native work under bounded deadlines before releasing its charges.

Session close and collection share one cleanup path. They stop admission, abort uncommitted transfers, cancel or settle active operations, terminate owned commands, remove daemon-owned output/temp data and retire the Session. Closing one adapter does not stop its shared daemon.

## Draining and Shutdown

Operator shutdown, Host-owned stdio EOF or unrecoverable daemon failure begins drain. There is no EIP daemon-shutdown method. An externally operated daemon does not stop merely because it has no Sessions.

Drain stops new admission, closes Sessions through the same cleanup path, waits boundedly for native cleanup and closes transport/runtime ownership. Failure is reported without claiming that unknown descendants or undeleted bytes are gone. The Host can tear down the outer sandbox when stronger guarantees are required. Resource locks do not remain held across native waits.

## Observability and Failures

Useful observations are Device/generation, transport state, Session counts, charged resource totals, collection counts and cleanup failures. Do not include credentials, full command text, output or native paths in ordinary diagnostics.

Malformed requests, stale selectors and quota exhaustion fail at the relevant boundary. One Session's failure is not a device-wide failure. Cleanup uncertainty remains charged and is retried boundedly; forgetting ownership is not reclamation. Authentication and framing remain necessary even though the Host is trusted for all folders.

Daemon JSON `egress.enabled` defaults to false and enables [controlled Session preparation](07-execution-isolation.md#controlled-session-egress). It carries no secret values. Ordinary Session launch policy remains unchanged.
