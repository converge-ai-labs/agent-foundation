# Resource Lifetime and Reclamation

## Design Position

The Session is the unit of native resource ownership and abandonment cleanup. It owns commands, output, transfers and operation history. Resources are not imported into other Sessions or kept alive through independent resource leases. The daemon retains aggregate accounting, the Session registry and any required worker/broker ownership. Isolated workers use the same Session engine, not a second lifecycle implementation; the outer Session owns attachment and expiry.

A long-lived daemon collects abandoned Sessions and completed history periodically and when resource usage reaches a high watermark. Per-Session and global limits are generous but finite. Collection does not delete workspace files, replay operations or evict healthy active work merely to admit new work.

## Session Liveness

A Session has one owning Host execution context spanning Runs. Accepted requests refresh last owner activity; clients send `session.keepalive` while a borrower is active. Final release touches the Session and stops keepalive, starting its idle window.

`session.open.configuration.lifecycle` selects an immutable `idle_timeout_ms` (default 1 hour) and optional `max_lifetime_ms` (default unlimited, hard deadline even during active work) measured on the daemon's monotonic clock. Attach and keepalive leave creation time unchanged; browser presence, output, CPU, sibling traffic, and WebSocket ping/pong count as non-owner activity.

- Active keepalive keeps quiet owned jobs alive; missing keepalive expires the Session at `idle_timeout_ms` even if processes keep producing output.
- Framed carrier loss or `session.detach` detaches the Session and aborts incomplete transfers while preserving commands and existing deadlines; the authenticated Host can reattach in the same generation before expiry with a strictly newer `owner_epoch` (preserving working directory/policy/resources without restoring transfers).
- HTTP connection loss leaves the Session attached until close or idle expiry. Explicit close starts cleanup immediately.

The Host persists Session identity separately from live clients and replaces a proven expired Session or lost daemon generation for new work without replaying uncertain commands.

### Ownership Transfer

A new Session starts at `owner_epoch = 1`. Every Session control request carries `eip_owner_epoch`; Device requests omit it. `session.attach` requires a strictly newer epoch and returns it in the descriptor. Other requests must match the installed epoch and attachment. Stale requests fail with `conflict` without refreshing activity or changing resources. The authenticated Host allocates and persists takeover epochs under its own execution ownership; the epoch fences obsolete owners, not authorization between independent principals.

Takeover serializes with request admission and attachment cleanup at the outer Session boundary, aborts old incomplete transfers, installs the new attachment and fences delayed old-carrier cleanup. It rejects subsequent admissions from the old owner; already-admitted native operations settle under their original identity without blocking takeover. The private isolated-worker carrier executes outer-admitted requests without a second public ownership fence. The Host fences result publication separately in its durable store. Lost attachment responses do not authorize command replay. Native operation identity and evidence remain unchanged across attachment.

## Session Cleanup

Open/attached, detached, closing and closed are sufficient lifecycle distinctions. Initial readiness gates ordinary operations. Close, idle expiry and maximum-lifetime expiry atomically move the Session to closing; later keepalive or attach cannot resurrect it.

One cleanup path:

1. Reject new Session work and abort readers and pre-commit writers.
2. Request cancellation of active commands and other cancellable operations.
3. Settle accepted file publication with its frozen destination and retain the strongest known outcome; close is not rollback.
4. Close stdin and terminate owned native process targets, with bounded waits and truthful cleanup results.
5. Remove owned output, completed records and temporary data, then retire the Session.

Native work still settling keeps its accounting owner until cleanup is proved. A closed selector need not retain a permanent tombstone. A later request receives `session_expired` or an invalid-selector result. Unknown cleanup outcomes remain failures rather than freeing capacity by forgetting them.

### VM Reclamation

A VM Session owns its VM, private SDK storage and registered staging directories on read-write Host mounts. Its lifecycle task retains ownership across cancelled startup requests, guest failures and private-carrier loss. It first removes the SDK VM, then reclaims registered staging and private storage. Session capacity remains charged until physical reclamation succeeds; a guest cleanup acknowledgement alone cannot release Host storage charges. Failed cleanup remains owned and retryable without blocking sibling Session control.

VM startup admission has an overall deadline and reports the phase that failed or timed out. Timeout or caller cancellation ends admission, not physical ownership: an in-flight SDK phase settles before removal, and abandoned startup does not advance to later phases. A stuck SDK phase can retain its reservation beyond that deadline. Cleanup failures report the affected private state and retry with bounded backoff; they never free accounting by forgetting the resource.

Before any SDK side effect, Envd durably records the backend and private Session storage in the Device generation. After daemon restart, Envd uses those records to remove surviving VM state and registered staging before publishing the next generation. It does not resume a guest or replay an operation. Reclamation requires the backend that created the record; an unavailable backend or uncertain cleanup fails generation preparation rather than forgetting ownership.

Each read-write mount has a registered staging directory with recorded source and directory identities. Cleanup removes only that directory's registered contents after VM removal, verifying the recorded identities. A moved or replaced registered directory keeps cleanup unresolved. Bytes moved out or independently hardlinked elsewhere are ordinary workspace state and are not followed or removed. Published files and unrelated workspace entries survive reclamation.

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
