# Validation and rollout

> Discussion proposal, not the current specification. See [status and scope](README.md).

## Capacity model

The estimate assumes 1,000 executing Runs, each completing one model/tool cycle every 30 seconds, which is about 67 boundaries per second. These are estimates, not measurements.

| Written at every boundary | Destination  | Total at 67 boundaries/s |
| ------------------------- | ------------ | ------------------------ |
| Complete state, 1 MB      | Object store | About 67 MB/s            |
| Complete state, 4 MB      | Object store | About 270 MB/s           |
| Changed rows, 10–30 KB    | PostgreSQL   | About 0.7–2 MB/s         |

A bounded state is not a cheap one: writing it whole costs the context size at every boundary. Writing only changed rows puts a moderate load on PostgreSQL:

- One short transaction per boundary, as today. The per-boundary cleanup outbox row, which today is inserted, claimed, settled, and purged, disappears.
- WAL grows by the changed bytes. Values over 2 KB are compressed by TOAST.
- Staged data is at most one context per executing Run plus less than one page of items: about 1–2 GB at 1 MB contexts. It is deleted when a Run merges or a page is archived.

## Measurements

Synthetic histories, each cycle with about 600 characters of reply, one tool call, and a 3,000-character tool result. Pydantic AI 2.51.0 on a 4-core x86_64 container:

| History                | Today: Harness export | Today: re-serialization | Today: SHA-256 | Proposed: encode each message | Proposed: compare |
| ---------------------- | --------------------- | ----------------------- | -------------- | ----------------------------- | ----------------- |
| 1.2 MB, 501 messages   | 7.1 ms                | 10.1 ms                 | 0.8 ms         | 8.9 ms                        | 0.10 ms           |
| 4.9 MB, 2,001 messages | 34.6 ms               | 51.0 ms                 | 3.2 ms         | 32.6 ms                       | 0.38 ms           |

Today's boundary also serializes up to 8 MiB of Display and uploads both objects; those costs disappear. Histories with images or files encode proportionally to their bytes, as they do today. The load test confirms these figures with real histories.

## Guardrails

- Content parts over the threshold are stored once as objects, so every row has a byte bound.
- A Run whose staged bytes exceed the cap merges early.
- Staged rows of a Run are deleted in one statement; autovacuum reclaims the space.
- Metrics: boundary commit duration, staged bytes per Run and in total, merges by trigger, change-detection time, and the `run_archive` backlog. The load test also tracks WAL rate and replication lag.

## Tests

- **Reconstruction equivalence.** At every boundary, the start object plus the staged rows reproduces the exported state byte for byte. Cover request overlays, cold-start trimming, compaction, adjacent-request merging, interruption, steering, recovery, fork, resume, and inline delegation.
- **Merge crash points.** Before upload, after upload and before commit, and after commit.
- **Fencing.** A stale attempt or lost lease never writes staged rows.
- **History pages.** Reads racing page publication, job crashes before and after its commit, and continuation of an interrupted item as a new item.
- **Load.** PostgreSQL at the capacity model above.

## Delivery order

1. **State slimming.** Remove `a13n.usage` from persisted state, add the subagent state binding and registry with validation on resume, and add checkpoint size and duration metrics.
1. **Staging.** Stage running state and visible items together, with merges, history pages, the paged items API, and Console paging. Both change the boundary commit, and only together does tool execution stop waiting for object storage.

## Decisions

Agreed in the #849 discussion:

- All three parts are in scope now, in the order above.
- Visible history is kept permanently in history pages.
- An interrupted item that a later attempt continues becomes a new item.
- The change is a clean breaking cutover: no compatibility readers, conversions, or mixed-version operation. Deployments start from a fresh store ([cutover](03-storage-and-apis.md#cutover)).
- The items API changes once, coordinated with #815.
- Rewrites of earlier history, including compaction, merge directly to a new start object. Rewritten context is never staged.
- Running-state staging and visible-item staging stay in separate tables, because their keys, lifetimes, and readers differ.
- Initial values: 256 items or 1 MiB per page, content objects over 256 KiB, and a 32 MiB staged-state cap per Run, tuned by the load test.

## Open questions

- **Change-detection cost.** Keep complete per-message encoding at every boundary in the first version, or adopt the no-in-place-edit invariant and encode only changed messages. See [01](01-running-state.md#change-detection-cost).

## Dropped from the earlier draft

Review removed these parts of the earlier journal draft because none of the three problems requires them:

- A semantic event journal with reducers, historical state inspection, and per-event Display.
- A Session-wide sequence and control journal, and usage events in the journal.
- A PostgreSQL-to-object-store journal archiver and permanent retention of every checkpoint.
- Hosting synchronous subagents in separate Threads with durable child waits.
