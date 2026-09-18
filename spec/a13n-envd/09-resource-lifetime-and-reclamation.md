# Resource Lifetime and Reclamation

## Design Position

The Session is the unit of native resource ownership and abandonment cleanup. It owns commands, output, transfers and operation history. Resources are not imported into other Sessions or kept alive through independent resource leases. The daemon retains only aggregate accounting and the Session registry.

A long-lived daemon collects abandoned Sessions and completed history periodically and when resource usage reaches a high watermark. Per-Session and global limits are generous but finite. Collection does not delete workspace files, replay operations or evict healthy active work merely to admit new work.

## Session Liveness

A Session has one owning Host scope. Accepted requests update its last owner activity. The client sends `session.keepalive` while that scope exists, independently of command waits and output traffic. A shared Device connection must not keep every historical Session alive automatically.

The daemon advertises a finite `idle_timeout_ms` and a shorter `disconnect_grace_ms`. The client sends keepalive comfortably before idle expiry. Envd uses a monotonic clock. Browser presence, output production, CPU usage, sibling traffic and WebSocket ping/pong are not Session owner activity.

- An owned Session with current keepalive remains live even when its command is quiet for hours.
- A Session without owner activity expires at its idle deadline, even if an abandoned process continues producing output.
- Loss of a framed carrier detaches its Sessions and starts a short grace window, capped by their existing idle deadlines. It immediately aborts that carrier's incomplete transfers but does not immediately kill commands.
- The same authenticated Host may reattach an existing Session in the same generation before its deadline. Reattachment retains its original working directory and resources and cancels the detach deadline. It does not create a second owner or restore transfers.
- HTTP connection loss alone is not Session detachment because TCP connections are not Session owners. The same selector remains usable until close or idle expiry.
- Explicit close has no grace period. It starts cleanup immediately.

The client stops keepalive and closes its Session when its owning adapter ends. A lost open response leaves an unclaimed Session that idle expiry collects. Reattachment is an explicit connection-runtime action, never automatic operation replay or a promise to recover Host state after restart.

## Session Cleanup

Open/attached, detached, closing and closed are sufficient lifecycle distinctions. Initial readiness gates ordinary operations. Close, idle expiry and grace expiry atomically move the Session to closing; later keepalive or attach cannot resurrect it.

One cleanup path:

1. Reject new Session work and abort readers and pre-commit writers.
2. Request cancellation of active commands and other cancellable operations.
3. Settle accepted file publication with its frozen destination and retain the strongest known outcome; close is not rollback.
4. Close stdin and terminate owned native process targets, with bounded waits and truthful cleanup results.
5. Remove owned output, completed records and temporary data, then retire the Session.

Native work still settling keeps its accounting owner until cleanup is proved. A closed selector need not retain a permanent tombstone. A later request receives `session_expired` or an invalid-selector result. Unknown cleanup outcomes remain failures rather than freeing capacity by forgetting them.

## Completed History and Pressure

Completed processes, terminal output and operation evidence have finite inactivity retention within a live Session. Explicit access updates their last-use time; generic keepalive does not refresh all history. A live command's output cannot be collected independently of that command.

The daemon performs a periodic sweep and triggers collection before rejecting allocations at a high watermark. It first removes expired/abandoned Sessions and expired completed history. Under pressure it may remove least-recently-used completed history earlier, provided no active read, wait, cleanup or response handoff uses it. Terminal process records and their output are collected as a group when still associated. Explicit release remains available for prompt cleanup.

Active operations and live commands in an owned Session are not pressure-eviction candidates. A healthy Session is not closed merely because it has no current command. If eligible reclamation is insufficient, admission returns a capacity error. No promise of minimum completed-history retention is made: the configured retention is an upper bound in the absence of renewed access, not a durable archive guarantee. The Host copies important output into its own storage.

Operation evidence needed to control a live command is retained with that command. When terminal history is evicted, its associated evidence may be evicted too. Missing evidence does not prove non-dispatch and does not authorize mutation replay. Unrelated Session traffic cannot preserve abandoned history.

## Accounting and Concurrency

Logical closure and physical reclamation are distinct. Running native work, spool files and staged bytes stay charged until their cleanup succeeds. Collection uses bounded work and does not hold registry locks across native waits. Failure in one cleanup does not block unrelated Session control.

Attach/keepalive versus expiry, and read/release versus collection, serialize at the existing Session/resource owner. Whichever transition wins determines the result; no separate lease broker or reference-import registry is introduced. Already admitted readers either finish under their bound or receive an explicit failure before deletion.

The daemon exposes resource totals and collection/cleanup failures for diagnosis. Exact watermark ratios, sweep cadence and default capacities are tuning decisions; they do not change eligibility or justify killing active owners.

## Invariants

1. Quiet owned jobs are not orphaned; noisy abandoned jobs are not immortal.
2. Session close or expiry cleans all of its resources without touching sibling Sessions or workspace files.
3. Reattachment preserves one existing Session; it never transfers resources into another.
4. Periodic collection prevents abandoned history from filling a long-lived daemon; pressure collection cannot bypass active ownership.
5. Failed cleanup remains accounted for, and uncertain native effects remain uncertain.
