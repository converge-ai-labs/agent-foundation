# Operate sessions and retained output

Envd owns one Environment generation and one initialized EIP session at a time. Operators manage daemon/process/bootstrap lifetime; EIP clients manage operation and transfer completion. Neither a live socket nor a retained output preview is a durable Agent checkpoint.

## Establish readiness

1. Create the protected runtime parent and configure exact mounts, execution policy, and carrier.
2. Start the daemon and perform EIP initialization within its admission window.
3. Inspect the returned descriptor and negotiated methods.
4. Request the required readiness before dispatching work.
5. Keep session/generation identity with operation evidence, not in an unrelated global process map.

The current internal initialization window is 30 seconds and the admitted session idle TTL is 30 minutes. These are operational policy constants, not additional accepted JSON configuration fields or promises for every release. HTTP can close an idle admitted session; reconnecting a carrier is not proof of a new operation's authority.

Only one active initialized session is admitted. If another session is busy after an abandoned connection, reconcile or wait for its lifecycle rather than forcing a parallel owner into the same daemon. Another mutually untrusted workload needs its own daemon/runtime boundary.

Use the [Python EIP client](python-client.md) for initialized sessions, exact method tables, transport defaults, timeout handling, and descriptor narrowing. The client and daemon can have different timeout defaults.

## Plan output capacity before launch

Standalone output defaults are:

| Limit                     | Default |
| ------------------------- | ------- |
| Inline preview            | 2 MiB   |
| Each stdout/stderr stream | 256 MiB |
| Shared spool              | 1 GiB   |

Before launching a command, the daemon reserves capacity for **both streams**. With these defaults, a live pair reserves 512 MiB; two such pairs can exhaust the 1 GiB spool before additional charges. Retained records can reduce available capacity further. A small preview does not reduce this reservation.

A busy response can therefore indicate output-reservation pressure, not a CPU or network failure. A finite output cap is not a request to evict other retained records. Increase quota deliberately, release no-longer-needed observations, or reduce the configured per-stream reservation while maintaining `spool >= 2 * per_stream` and `preview <= per_stream`.

Python Local Envd and Docker Providers supply different defaults. Configure their [Provider fields](../environments/configuration.md) when they own bootstrap rather than editing the standalone daemon table.

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
| Initialization/session admission busy    | Existing session owner, admission lifecycle, abandoned carrier, and idle expiry        |
| Required method absent                   | Descriptor narrowing, mount permissions, command eligibility, and isolation readiness  |
| Busy / quota error before command launch | Per-stream pair reservation and retained spool charges                                 |
| Partial preview                          | Returned reference/range and producer/completion evidence                              |
| Isolation failure                        | Run the bounded [isolation probe](isolation.md) under the ordinary daemon account      |
| Unknown command/write outcome            | Original operation receipt and replay/reconciliation class; do not retry as new intent |
| Remote target unavailable                | Provider/backend access and exact saved target identity, not another mount's fallback  |

The daemon has no general health, browser, or arbitrary HTTP endpoint. HTTP EIP exposes its authenticated control/transfer routes; readiness is an EIP operation. Keep diagnostics off stdio protocol stdout. [Configuration](configuration.md) owns carrier/bootstrap fields and [isolation](isolation.md) owns platform prerequisites.
