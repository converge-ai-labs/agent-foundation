# Implementation scope and validation

> Discussion proposal, not the current specification. See [status and scope](README.md). This is a proposed implementation sequence and acceptance criteria, not a record of completed implementation or measured results.

## Changes to make together

The implementation is a coordinated breaking change across the Harness integration, Service, and its consumers. Develop it in coherent steps, but expose one resulting design rather than maintaining old and new persistence paths in production.

1. **State/event contract.** Define typed event payloads, deterministic state reducers, controlled mutation reporting, and immutable bounded state captures. Separate current continuation data from historical Usage and nested child state. Cover message changes, Capability mutations, environment descriptors, pending work, and cursor updates.
2. **Durable journal.** Add stream metadata, recent events, chunk directory, and retained checkpoint records. Couple event append with the existing lifecycle, inbox, child, and Usage transactions. Add bounded producer acknowledgements and Session ordering. Generate and review schema changes through the repository's normal migration workflow.
3. **Archival and reads.** Implement the control-owned archive and cleanup sweeps, immutable chunk encoding, and one consistent PG/S3 reader. Retain old published chunks and snapshots; collect only provably unpublished objects. Exercise readers concurrently with publication and cleanup.
4. **Periodic checkpoints and recovery.** Replace the current paired state/Display upload at each boundary with event commit, periodic snapshot requests, exact-position publication, and log replay. Make terminal checkpoint work durable so a control sweep can finish it. Update inbox incorporation evidence to journal positions rather than making consumption wait for object upload.
5. **Hosted child execution.** Move synchronous children to independent Threads, change resumable waiting to an open Run, release worker slots, and make original-call completion distinct from asynchronous notification. Keep accepted bindings fixed across all Attempts.
6. **Presentation and consumers.** Replace full Display reads and folds with event projection and bounded pages. Update Console live reconciliation, state/event inspection, OpenAPI, and the exported stream schema. Coordinate SDK changes in their owning repositories; do not vendor SDK implementations here.
7. **Removal and specification adoption.** Remove obsolete full Display objects, truncation/fold persistence, nested hosted child state, persisted Usage accumulators, superseded cleanup assumptions, and old transport recovery paths. Only an accepted implementation change updates the owning Service chapters and makes the resulting rules normative.

There is no historical Run or Display conversion, dual reader, compatibility flag, or separate “V2” implementation. Schema evolution still uses normal tooling; lack of historical compatibility is not a reason to hand-write migrations or to claim that the current code already implements the design.

## Current implementation surfaces

These links identify the baseline surfaces to revisit, not a requirement that the eventual modules keep exactly this layout:

| Surface                                                        | Relevant current code or contract                                                                                                                                   |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Run/Attempt/Usage tables and immutability                      | [runs/tables.py](../../../packages/a13n-service/a13n_service/runs/tables.py)                                                                                        |
| Checkpoint publication and reclamation                         | [runs/checkpoints.py](../../../packages/a13n-service/a13n_service/runs/checkpoints.py)                                                                              |
| Boundary commit, `resume_usage=False`, and input incorporation | [runs/execute.py](../../../packages/a13n-service/a13n_service/runs/execute.py)                                                                                      |
| Current Display folding                                        | [runs/display.py](../../../packages/a13n-service/a13n_service/runs/display.py)                                                                                      |
| Independent Usage ingestion                                    | [runs/usage.py](../../../packages/a13n-service/a13n_service/runs/usage.py)                                                                                          |
| Existing asynchronous child hosting                            | [runs/subagents.py](../../../packages/a13n-service/a13n_service/runs/subagents.py)                                                                                  |
| Portable Harness state                                         | [Harness state.py](../../../packages/a13n-harness/a13n_harness/state.py)                                                                                            |
| Inline delegation                                              | [Harness delegation.py](../../../packages/a13n-harness/a13n_harness/toolsets/delegation.py)                                                                         |
| Immutable object interface                                     | [objects/interface.py](../../../packages/a13n-service/a13n_service/infra/objects/interface.py)                                                                      |
| Accepted contracts affected by implementation                  | [Runs](../05-runs.md), [facts and delivery](../07-facts-and-delivery.md), [runtime](../09-runtime.md), [API](../10-api.md), [observability](../12-observability.md) |

The current `checkpoint_cleanup` behavior preserves only current Run pointers while reclaiming superseded objects. That is incompatible with retained historical checkpoints and journal references. Do not reuse a whole-prefix deletion rule for the new objects.

## Acceptance scenarios

| Scenario                                                     | Required result                                                                                   |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| PG rejects an event transaction                              | Neither event nor coupled business mutation is acknowledged                                       |
| Producer loses its response after commit, then retries       | One logical event batch and one business effect, even if its PG events were already archived      |
| Worker loses its lease                                       | No further execution-state commits; legitimate late Usage remains eligible                        |
| Archiver dies before upload completes                        | PG events remain readable and another archiver can retry                                          |
| Archiver dies after upload but before catalog commit         | PG events remain; the object is not visible history                                               |
| Catalog commits and archiver dies before deletion            | Readers use the published chunk; duplicate PG copies are safely removed later                     |
| Two archivers overlap                                        | One committed contiguous range; no overlapping published chunks                                   |
| A reader races archive publication and cleanup               | Same ordered events, with no missing or duplicated positions                                      |
| Checkpoint is ahead of archival                              | Unarchived events remain in PG despite being covered by state                                     |
| Archival is ahead of checkpoint                              | Recovery reads the missing suffix from S3 and then PG if needed                                   |
| Worker dies after terminal event but before final checkpoint | The terminal state remains reconstructable and a sweep can materialize it                         |
| Compaction occurs between two inspected events               | Both states reconstruct using the recorded replacement context; earlier Display remains available |
| Parent dies while a synchronous child is running             | Same child is rediscovered; no duplicate child; parent holds no worker slot                       |
| Child completion races parent cancellation                   | One consistent terminal/wait outcome; cancelled parent is not revived                             |
| Usage is retried, refined, or reported late                  | Correct ledger total and one journal event per accepted observation; no execution-state mutation  |
| Redis loses every live entry                                 | Committed Display catches up from PG/S3 without waiting for a new checkpoint                      |
| Client loads only a completed tool event                     | Complete bounded presentation without earlier tool events or state replay                         |
| S3 fails for an extended interval                            | Durable PG backlog until configured backpressure; no acknowledged history disappears              |
| A tool may have completed externally before a crash          | Recover by a supported idempotency contract or stop; never blindly repeat it                      |

Reducer tests should compare state reconstructed at selected positions against independently captured live coordinator state, including nested mutations, intermediate changes, messages, compaction, and pending calls. They should establish semantic equality rather than merely assert that serialization and deserialization call the same helper.

Use real PostgreSQL concurrency tests for transaction, lease, cleanup, and reader races. Use fault-injected object operations for publication/crash cases. Exercise forward/backward/range pagination across chunk boundaries and the moving PG/S3 boundary, including empty filtered pages, large referenced content, and interleaved child streams. Keep a real Console flow for reload, cancellation, Attempt takeover, and child waiting.

## Performance model

Measure the full execution path, not only the count of S3 objects. Record worker-to-PG bytes, object-store upload/download bytes, serialization CPU, committed events per second, acknowledgement latency, database statements/WAL, archive lag, cleanup lag, checkpoint size/time, recovery latency, and bytes fetched per Display page. Compare equivalent conversations including Usage and child execution.

For an illustrative object-upload model, let:

- `M` be the number of former full-checkpoint boundaries;
- `S` be the average bounded state size after removing history and nested children;
- `K` be the checkpoint event interval, assuming roughly one counted mutation per boundary for this example;
- `J` be the total encoded journal bytes, excluding separately referenced large objects.

The old state-upload term is approximately `M × S`, plus repeated full Display uploads and any larger old Usage/child state. The proposed term is approximately `ceil(M / K) × S + J`, plus required base/final snapshots and separately stored content. Compression, compaction, and byte-triggered snapshots change the actual result.

For 1,024 boundaries, 1 MiB states, a 32-mutation interval, and 8 MiB of journal data, the simplified comparison is 1,024 MiB of repeated state uploads versus 32 MiB of periodic state uploads plus 8 MiB of log chunks. This is a capacity example, not a measured speedup. It excludes PG traffic/WAL, snapshot construction reads, Display reads, and mandatory extra snapshots. Events still travel to PG and later to S3.

Checkpointing is valuable because it bounds replay and avoids repeatedly uploading unchanged state. Archiving is valuable because it moves the durable tail out of PG. They solve different costs, which is why independent schedules remain the default.

## Initial tuning and remaining risks

The architecture above is selected for this proposal. The remaining items are tuning and implementation proof, not invitations to add another persistence backend or compatibility layer.

| Item                  | Initial approach                                                                                | What to measure or prove                                                     |
| --------------------- | ----------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Snapshot interval     | Evaluate 32 state mutations, plus replay-byte bounds and mandatory compaction/terminal requests | Recovery latency, state size, and upload bytes                               |
| Archive batching      | Evaluate roughly 1 MiB of encoded events or 30 seconds for the oldest pending event             | Small-object overhead, PG live bytes, and read amplification                 |
| Event/page limits     | Bound encoded/decoded bytes, inline content, item count, and scanned work                       | Real model/tool payload distribution and client memory                       |
| PG cleanup            | Ordinary bounded deletes and autovacuum                                                         | Sustained ingest/delete throughput, WAL, table/index growth, and replica lag |
| Session ordering      | One short transactional counter per Session                                                     | Contention in Sessions with many parallel children                           |
| Session range reads   | Chunk range directory and ordered merge; no persistent Display index                            | Sparse overlap and chunk fanout under realistic child histories              |
| State instrumentation | Controlled mutations and complete typed reducers                                                | Missed nested changes and independently captured state equivalence           |
| Durable waits         | Open waiting Run, fenced reclaim, durable delivery evidence                                     | Approval/child/cancellation races and worker starvation                      |
| Object retention      | Retain committed journal and checkpoint references                                              | No current cleanup path deletes reachable history                            |

A single slow Session counter or high chunk fanout should first be measured under the accepted concurrency bounds. PG churn should first be addressed with bounded batches, appropriate indexes, and vacuum settings. Partitioning, permanent per-event location indexes, extra materialized Display stores, and cross-store transactional machinery are not part of the initial design.

The unavoidable limits remain visible: raw uncommitted fragments may be lost, unknown external effects cannot be rolled back, unreported charges cannot be recovered from nothing, and retained catalog/checkpoint metadata grows with history. None of those limits is hidden behind a claim of universal replay or exactly-once execution.
