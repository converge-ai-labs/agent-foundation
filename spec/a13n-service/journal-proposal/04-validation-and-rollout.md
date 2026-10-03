# Validation and rollout

> Discussion proposal, not the current specification. See [status and scope](README.md).

## Capacity

The estimate assumes 1,000 executing Runs, each completing one model/tool cycle every 30 seconds, which is about 67 boundaries per second. These are estimates, not measurements.

| Written at every boundary                                    | Total at 67 boundaries/s |
| ------------------------------------------------------------ | ------------------------ |
| Today, once Display reaches its cap: about 9–10 MB           | About 600 MB/s           |
| Proposed, 1 MB of text context, uncompressed                 | About 67 MB/s            |
| Proposed, 1 MB of text context, compressed 3–5 times smaller | About 13–22 MB/s         |

The tail and an occasional page add little. Large binary content is uploaded once when it first appears, and that upload delays the next model request once. A checkpoint still uploads the whole text context, so the cost grows with the context, not with the change; tool execution still waits for that smaller upload. If measurements show it dominates, the next step is [incremental state](#incremental-state-deferred).

## Measurements and metrics

- The load test measures checkpoint bytes before and after compression, checkpoint duration, compression time, and boundary latency with real histories, including computer-use screenshots.
- Metrics: checkpoint bytes and duration, content objects written per Run, tail size, page writes, and the cleanup backlog in the existing `a13n.backlog.size` and `a13n.backlog.oldest_age`.

## Tests

- **State round trip.** Exporting through the storage binding and loading again reproduces the same messages, binary content, namespaces, and environment states.
- **Usage.** No persisted Service state contains `a13n.usage`, and accounting is unchanged.
- **Inline subagents.** A child's state is saved once when it ends and not when it is cancelled. Starting a parent Run reads only the registry. `resume_subagent` loads the state and checks only that child's definition. Fork rewrites Thread IDs and forks the child state on its first load. The default store keeps only values in the reference closure. A worker failure during a delegation repeats the tool call, and a state saved by the failed attempt is removed by the seal cleanup.
- **Visible history.** Paging across pages and the tail; at each checkpoint only the primary Run's unanswered tool calls stay open; a child that fails after starting a tool call, followed by 300 finished items, still produces pages and leaves the tail small; after a takeover that item is `interrupted` and still pages; a takeover continuing an open tool call in place; an event for an item already in a page fails the fold; a waiting seal leaving the approval-pending call readable as `interrupted`; a page and its tail committed in one transaction; reads racing a checkpoint.
- **Continuation.** The next Run after a failed or cancelled Run starts from its last checkpoint; a Run started from a failed Run's tool-boundary checkpoint gives each unanswered call the interrupted result, runs none of them, and places its input after those results; fork from failed and cancelled Runs; child results whose origin failed.
- **Cleanup.** Reclamation on replacement, takeover scans of `state/` and `tail/`, and the seal scan's keep set; inherited content and subagent states keep resolving after the Run that wrote them seals; a screenshot referenced only from a saved inline child state survives the seal scan and loads when a later Run resumes that child.

## Delivery order

1. **State slimming.** Remove `a13n.usage` from persisted state, add the storage binding for inline subagent states and large binary content, compress state objects, and add checkpoint size and duration metrics.
2. **Visible history.** Ordinals, pages and the tail, the paged items API, and Console paging.
3. **Continuation.** The head, fork, and child-result rules, and `tool_recovery="never"` for Runs that start from failed or cancelled Runs.

## Decisions

Agreed in the #849 discussion:

- Every boundary keeps writing a complete state object. This proposal adds no staging, change log, or replay.
- The Service stops persisting `a13n.usage`. Inline subagent states and large binary content go through a Host storage binding, and exported state lists its complete reference closure. State objects are compressed. The first upload of a new large content part delays the next model request once; later checkpoints carry only its reference.
- An inline child's state is saved once when the child ends and referenced from the parent's registry. Its definition is checked only when it is resumed. Inline children are not checkpointed while they run; a worker failure during a delegation repeats the tool call.
- Display becomes permanent history pages plus a tail written with each checkpoint. Pages hold only final items: at each checkpoint only the primary Run's unanswered tool calls of its latest model response stay open, and the worker marks every other unfinished item `interrupted`.
- Every sealed Run becomes the head. Fork accepts failed and cancelled Runs. A Run that starts from a failed or cancelled Run uses the Harness's existing `never` tool recovery, so unfinished tool calls get an interrupted result and none runs again. Automatic advancement still pauses after a failed or cancelled Run.
- Objects belong to the Run that writes them. The seal scan keeps the final state, its reference closure, the final tail, and the pages.
- This proposal owns the items API change and Console paging; #788 is not a prerequisite.
- The change is a clean breaking cutover for the Service and Harness state formats ([cutover](03-storage-and-apis.md#cutover)).
- Initial values: 256 items or 1 MiB per page and a 64 KiB large-content threshold, tuned by the load test.

## Incremental state (deferred)

If measured checkpoint uploads dominate, the next step is a state change log. The discussion settled what such a log must look like:

- **Only an exact log is acceptable.** Each record is the byte-level difference between consecutive exported states, with the hash of the state after it. Before writing a record, its producer checks that applying it to the previous state reproduces the current one, and writes a complete state instead if it does not.
- **A log of semantic facts is not safe on Pydantic AI today.** pi and Codex record facts and rebuild the model context from them. Pydantic AI instead writes history-processor output back into the message history and edits messages in place, and some Harness components do the same. Replaying recorded facts could therefore produce a different state.
- **Layout considered.** One log per Thread, with recent records in PostgreSQL and older segments in object storage; complete-state records within the log serve as snapshots; fork references a position.

## Dropped from earlier revisions

Review removed these designs because the problems above do not require them:

- A semantic event journal with reducers, per-event Display, and a Session-wide sequence (first draft).
- PostgreSQL staging of running state with merges and post-seal merges, and staged visible items moved to pages by a background job (second revision).
- Continuing an interrupted item as a new item.
