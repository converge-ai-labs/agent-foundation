# Operate sessions and retained output

Envd owns one Device generation with multiple independent Sessions. Operators manage daemon/process/bootstrap lifetime; each EIP Session owns its operations, processes, transfers, and retained output. Neither a live socket nor a retained output preview is a durable Agent checkpoint.

## Establish readiness

1. Create the private runtime parent and configure Device identity, default working directory, executable policy, limits, and carrier.
2. Start the daemon and initialize a Device connection; initialization creates no Session.
3. Inspect the Device descriptor and optionally browse directories without opening a Session.
4. Open a fresh Session with a fixed Device working directory and required methods, then establish readiness before dispatching work.
5. Keep Device generation and Session identity with operation evidence, not in an unrelated global process map.

The admitted Session idle TTL defaults to 30 minutes. Keepalive, disconnect grace, and exact same-Session attachment follow the advertised lifecycle. Reconnecting a carrier does not create or recover an operation's authority.

Session admission and aggregate Device quotas bound concurrency. Closing one Session cleans up only its resources and preserves its siblings. A fixed working directory is not a filesystem security boundary: Sessions can access other Device paths allowed by the daemon account. Mutually untrusted workloads need separate outer account, container, or sandbox boundaries.

Use the [Python EIP client](python-client.md) for Device ownership, scoped Sessions, exact method tables, timeout handling, and descriptor narrowing. The client and daemon can have different timeout defaults.

## Plan output capacity before launch

Standalone output defaults are:

| Limit                     | Default |
| ------------------------- | ------- |
| Inline preview            | 2 MiB   |
| Each stdout/stderr stream | 256 MiB |
| Each Session spool        | 1 GiB   |
| Aggregate Device spool    | 4 GiB   |

Before launching a command, the daemon reserves capacity for **both streams**. With these defaults, a live pair reserves 512 MiB; two such pairs can exhaust the 1 GiB spool before additional charges. Retained records can reduce available capacity further. A small preview does not reduce this reservation.

A busy response can therefore indicate output-reservation pressure, not a CPU or network failure. A finite output cap is not a request to evict other retained records. Increase quota deliberately, release no-longer-needed observations, or reduce the configured per-stream reservation while maintaining `spool >= 2 * per_stream` and `preview <= per_stream`.

Python Local Envd supplies its own preview and file-value defaults through Host launch configuration; Docker uses a separate native runtime. Configure the owning [runtime fields](../a13n-environment/configuration.md) rather than treating Session selection as daemon bootstrap.

## Preview, retained bytes, and completion

A command result or process read can contain only a preview. Read its retained output reference with bounded offsets/cursors and preserve available ranges, provenance, completion flags, and truncation evidence.

- Command completion is not the same as complete output delivery.
- Reaching the currently retained end is not always producer EOF.
- Repeated reads do not consume retained content or restore omitted bytes.
- Process inspection does not imply output attachment or reset a retention budget.
- Release process/output observations explicitly when they are no longer needed, using the owning EIP method. Release is not equivalent to killing a live command.

For output that must survive daemon/session retention, use application-managed files or another durable logging destination. Do not use the private spool as an application storage API.

## Transfers and uncertain commands

File writes through the transfer API require explicit commit. Closing or abandoning a writer is not successful publication. Abort incomplete transfers, and use receipt/replay evidence to distinguish not-dispatched, known completion, and unknown outcomes.

A timeout or transport loss after dispatch does not prove a write or command failed to execute. Preserve the original operation identity where its replay class permits reconciliation. Never blindly relaunch a shell command to compensate for a missing acknowledgement.

Session loss and daemon-generation loss are distinct. A retained target may still exist even when local handles have expired; a new generation invalidates generation-bound handles and output selectors. Provider state can identify a target, but cannot recreate discarded process output or automatically restore all commands.

Graceful client/session close and Provider process cleanup follow their respective ownership rules. Do not use daemon shutdown as a substitute for a product-level Run interrupt, and do not assume an abrupt kill delivered final cleanup evidence.

## Diagnose failures at the right layer

| Symptom                                  | Check                                                                                  |
| ---------------------------------------- | -------------------------------------------------------------------------------------- |
| Initialization/session admission busy    | Device Session capacity, aggregate quotas, pending cleanup, and idle expiry            |
| Required method absent                   | Required-method selection, daemon account permissions, and executable policy           |
| Busy / quota error before command launch | Per-stream pair reservation and retained spool charges                                 |
| Partial preview                          | Returned reference/range and producer/completion evidence                              |
| Filesystem or command access denied      | Daemon account permissions and the Host's [outer security boundary](isolation.md)      |
| Unknown command/write outcome            | Original operation receipt and replay/reconciliation class; do not retry as new intent |
| Remote target unavailable                | Provider/backend access and exact saved target identity, not another mount's fallback  |

The daemon has no general health, browser, or arbitrary HTTP endpoint. HTTP EIP exposes its authenticated control/transfer routes; readiness is an EIP operation. Keep diagnostics off stdio protocol stdout. [Configuration](configuration.md) owns carrier/bootstrap fields and [security boundaries](isolation.md) explains Host-owned isolation.
