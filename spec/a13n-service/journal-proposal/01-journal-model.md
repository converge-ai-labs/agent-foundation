# Journal model

> Discussion proposal, not the current specification. See [status and scope](README.md).

## Execution identity and lifecycle

| Entity  | Meaning                                                                            | What it does not fix                     |
| ------- | ---------------------------------------------------------------------------------- | ---------------------------------------- |
| Session | Groups related Threads, their execution history, and Session control operations    | One permanent agent or model             |
| Thread  | Owns a sequential continuation history and selects the state base for its next Run | One permanent agent or model             |
| Run     | One logical execution with an immutable accepted selection                         | One worker process or one attempt        |
| Attempt | A fenced worker ownership interval within a Run                                    | New conversation history or new bindings |

A Run freezes the agent revision, model selection, execution options, environment and memory mounts, and other continuation-relevant bindings at acceptance. Provider credentials may refresh without changing that selection. Replacing a model, agent, or mount is a next-Run operation, including when the current Run is paused. The proposal introduces no mid-Run configuration switch or fallback to a different model. Child Runs make their own frozen selections from the accepted child binding.

A Thread has at most one open Run. The proposed Run states are `accepted`, `running`, `waiting`, `completed`, `failed`, and `cancelled`. Only the last three are terminal and sealed. `waiting` retains the open Run, its frozen selection, and a bounded pending continuation descriptor. Answering an approval or receiving a synchronous child result makes that Run claimable again. A worker handoff or crash also continues the same Run under a new fenced Attempt.

This intentionally replaces today's rule that `waiting` is sealed and resumed through a successor Run. It avoids introducing two different persistence models for waiting on a child and waiting on an approval. The Thread's current pointer retains an open waiting Run. Its history head advances only to a completed Run; a failed or cancelled Run remains inspectable but is not selected automatically as the next state base.

Terminal Runs accept no further execution-state mutations. Accounting reports may arrive later and append accounting events without changing the terminal outcome. The separate journal metadata row remains mutable for such appends and for archival progress; neither operation changes the sealed Run. Checkpoint materialization metadata, including its lease and latest pointer, may still advance when a background writer materializes the already committed terminal state. That storage-metadata update is the only new exception to sealed Run immutability; it cannot change selection, state coverage beyond the terminal position, or outcome.

## Streams and order

Each Run has one stream, including every synchronous and asynchronous child Run. Each Session also has one control stream for operations outside a Run: Session and Thread creation, queued input, desired configuration changes, mount changes, and archive requests. Thread-specific control events carry `thread_id`; they do not require another stream type.

Every event has two positions:

- `seq` is consecutive within its stream. It is the position used by Run replay, checkpoint coverage, and chunk boundaries.
- `session_seq` is consecutive across committed events in the Session, including its control stream and every child Run. It provides a stable merged presentation order.

Both are allocated in the transaction that commits the events. A rollback consumes neither range. A short per-Session counter lock serializes allocation and commit; it does not span model, tool, or S3 I/O. `session_seq` means **Service commit order**, not provider timestamp order or a claim about simultaneous real-world actions. A late fee appears at its later commit position and separately identifies the call it charges.

Use a single lock order: acquire required domain locks first, then the Session journal counter, then stream metadata rows in ID order. No path that takes a journal lock subsequently takes a domain lock. Archival operates on stream metadata without acquiring domain locks. This keeps journal ordering from introducing an inverted lock dependency.

For example:

| `session_seq` | Stream     | `seq` | Event                       |
| ------------- | ---------- | ----- | --------------------------- |
| 401           | Parent Run | 20    | Synchronous child requested |
| 402           | Child Run  | 1     | Child started               |
| 403           | Child Run  | 2     | Assistant message appended  |
| 404           | Child Run  | 3     | Child completed             |
| 405           | Parent Run | 21    | Child result accepted       |

A Session view can show this as one timeline. Parent state replay still reads only the parent stream; it never applies child state mutations to the parent.

## Event envelope

One concrete proposed record is:

```json
{
  "id": "evt_example_1042",
  "stream_id": "log_example_run",
  "session_id": "ses_example",
  "thread_id": "thr_example",
  "run_id": "run_example",
  "attempt_id": "att_example_2",
  "seq": 42,
  "session_seq": 1042,
  "kind": "tool.completed",
  "occurred_at": "2026-10-02T10:00:03Z",
  "recorded_at": "2026-10-02T10:00:04Z",
  "data": {
    "call_id": "call_example",
    "name": "shell",
    "arguments": {"command": "pwd"},
    "status": "completed",
    "result": {"text": "/workspace\n", "exit_code": 0}
  }
}
```

IDs here are illustrative. A production schema uses the repository's ID conventions and tenant integrity rules. `run_id` and `attempt_id` are nullable for control operations; control-originated Run events may also have no Attempt. A related input, child, call, usage record, or trace is identified explicitly in its payload. `occurred_at` is descriptive; sequence numbers determine order.

The envelope contains one typed `data` payload. A deterministic reducer defines the state effect of each event kind; a separate renderer defines its Display contribution. There is no second durable Display event stream. Kinds with no state effect are reducer no-ops. Kinds with no presentation value yield no Display item. Unknown kinds fail state replay rather than being silently ignored.

Large message parts, tool results, and attachments use immutable `{key, digest, size, content_type}` references. Their objects must exist before the referencing event commits. Each event contains enough public display data or immutable references to render independently. It cannot require fetching an earlier `tool.started` event merely to discover the completed tool's name or arguments.

## State mutations are explicit

The Harness integration needs a mutation interface that reports the actual changes at the point they occur. Snapshot comparison alone is insufficient. If a Capability changes `status` from `pending` to `running` and then `completed`, comparing only the first and last snapshots loses the `running` event.

The proposed state event vocabulary is small:

| Kind                                              | State effect                                                                          |
| ------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `run.accepted`                                    | Initialize the Run from its fixed compatible base and frozen bindings                 |
| `run.started`                                     | Record the transition into execution under those bindings                             |
| `messages.appended`                               | Append complete model-message values, identified by stable message IDs                |
| `messages.compacted`                              | Replace the active model context with the supplied bounded compacted context          |
| `capability.patched`                              | Apply ordered `set`, `remove`, or `append` operations within one Capability namespace |
| `environment.state_set`                           | Replace one bounded environment continuation descriptor; not its files                |
| `input.consumed`                                  | Advance input incorporation evidence exactly once                                     |
| `memory.cursor_set`                               | Advance one mounted memory's delivered change cursor                                  |
| `tool.started`                                    | Add a bounded pending call descriptor                                                 |
| `tool.completed` or `tool.failed`                 | Settle that descriptor and record its complete outcome                                |
| `child.requested` or `child.result_received`      | Add or settle a parent-to-child continuation reference                                |
| `run.waiting` or `run.resumed`                    | Set or clear the bounded wait descriptor                                              |
| `run.completed`, `run.failed`, or `run.cancelled` | Record terminal execution status and settle remaining continuation descriptors        |
| `recovery.boundary`                               | Mark a validated continuation boundary; no model or tool is executed by replay        |

Model dispatch also records `model.started` and `model.finished` events with a bounded pending request identity and outcome status. The complete resulting message is owned by `messages.appended`; neither model completion nor a tool outcome inserts that message a second time. A tool result can therefore be recorded before the corresponding model history update, with a later `recovery.boundary` identifying the resumable state. Every individual event position remains inspectable; not every such position is safe for execution.

For example, a task update in the existing optional Working State Capability can be expressed as follows. This does not introduce a mandatory task-management feature or a new task table.

```json
{
  "kind": "capability.patched",
  "data": {
    "namespace": "a13n.working-state",
    "changes": [
      {"op": "set", "path": "/tasks/task_17/status", "value": "completed"}
    ]
  }
}
```

This is an illustrative proposed persistence shape, not a claim that the current Capability has this exact wire layout. The patch writes one changed value. It does not serialize the entire task collection. Paths are JSON Pointers relative to the namespace; `set` assigns a value, `remove` deletes an existing entry, and `append` appends supplied values to an existing array. Namespace initialization uses an explicit root `set`. Invalid paths or types fail replay. Array changes are applied in their recorded order. Do not add general object-graph tracking, arbitrary reducer code in stored records, or patch inference for plugin memory.

The same rule applies to nested Capability data: mutations must be visible to the coordinator. A namespace containing an ever-growing historical collection does not become bounded merely because it supports patches. Historical records belong in the journal or their owning store; only bounded continuation data belongs in state.

## Event coverage

“What happened in a Session” means committed semantic product facts, not every process instruction, network packet, or token fragment.

| Source                        | Required journal coverage                                                                                                                |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Session and Thread operations | Creation, fork/child relationships, labels and caller-header changes, archive/cancel requests, desired configuration changes             |
| Input                         | Queued, assigned, consumed, withdrawn, or rejected input, with the relevant immutable content or reference                               |
| Run execution                 | Accepted selection, Attempt changes, messages, tool start/outcome, state mutations, waits, compaction, completion, failure, cancellation |
| Agent and model selection     | Requested next-Run change and the actual immutable selection accepted for a Run                                                          |
| Environments and memory       | Mount/unmount requests, effective Run bindings, relevant lifecycle outcomes, and continuation cursor changes                             |
| Subagents                     | Requested child, parent relation, execution mode, result availability, and parent consumption                                            |
| Accounting                    | Each accepted new or refined usage contribution, with stable ledger identity and sufficient recorded values                              |

Raw text, reasoning, and tool-argument fragments may be delivered live but are not durable journal entries. Their completed aggregates are recorded. On normal cancellation, an available meaningful partial aggregate can be recorded once with `interrupted` status before termination. A process crash may lose fragments that never reached a committed aggregate; that does not erase earlier committed events.

Do not record credentials, resolved authorization headers, or secrets from environment configuration. Record resource identity and permitted configuration facts. The journal is not a general-purpose replacement for security audit tables, telemetry, or a reconstruction mechanism for every relational table in the Service.

## Commit and acknowledgement

An execution boundary can batch several semantic events in one short PostgreSQL transaction. Their separate sequence numbers preserve the intermediate states even though the batch commits atomically. Flush at acknowledgement boundaries, before relying on a new external-call intent, at a bounded batch byte/count threshold, or after a short bounded delay. An interval timer alone does not authorize execution to run ahead of uncommitted recovery state.

The transaction commits the events, applicable inbox/lifecycle/child/accounting changes, producer acknowledgement, and new sequence positions together. Acknowledgement occurs only after commit. Execution-state producers must prove their current Attempt fence. Resource operations use their existing authorization and concurrency checks. Usage follows its separate late-ingestion rule.

Use bounded producer cursors rather than keeping an extra permanent deduplication row for every event. A single-writer Attempt retains its last accepted batch number and digest until it finishes; an uncertain commit retries the same batch before sending another. Domain commands use their existing durable idempotency evidence, child creation uses the originating call identity, and Usage uses its accounting cursor. Archiving an event must not erase the evidence needed to acknowledge a current producer retry. Older fenced Attempts cannot regain authority by retrying an archived event.

Trace IDs may be attached for diagnosis. Trace export is optional and does not acknowledge journal durability, authorize progress, or supply missing state effects.
