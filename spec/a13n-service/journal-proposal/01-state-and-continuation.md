# State and continuation

> Discussion proposal, not the current specification. See [status and scope](README.md).

## Checkpoints stay complete

Every safe boundary still writes one complete state object and commits it in the boundary's fenced transaction, together with input consumption, steer assignment, memory cursors, and usage ingestion ([`checkpoints.py`](../../../packages/a13n-service/a13n_service/runs/checkpoints.py)). Recovery loads the Run's last checkpoint, as today. Nothing is staged elsewhere and nothing is replayed.

What changes is the size of that object. Three things leave it, and it is compressed.

## What the state holds

A Run's continuation state remains today's `RunState` around a `HarnessState`: message history, Capability namespaces and environment states, plus the Service's sequence, attempt, the deferred requests of a waiting outcome, and resume-input progress.

- **`a13n.usage`** is removed from every state the Service persists. The Service never restores accounting from a checkpoint (`resume_usage=False`), and `usage_records` already holds every contribution. Harness UI still restores usage from state, so the Harness default does not change.
- **Inline subagent states** move to separate objects; see [inline subagent state](#inline-subagent-state).
- **Large binary content parts** move to separate objects; see [large content](#large-content).
- **Compression.** State objects are compressed with zstd.

What remains is message text, small Capability state, environment states, and the Service's progress fields. Text compresses well, so a boundary uploads a fraction of the text context.

## The storage binding

The Harness owns its state format, so it decides what is stored separately. A Host may supply a `state_store` in `RunBindings` ([`context.py`](../../../packages/a13n-harness/a13n_harness/context.py)):

```text
StoredRef                       # opaque to the Harness
  key, digest, size             # digest: SHA-256 of the saved bytes

StateStore
  save(data, kind) -> StoredRef # kind: subagent_state or content
  load(ref) -> data
```

The Harness uses the store for inline subagent states and large binary content parts. Exported state lists its reference closure: every saved value it needs, whether the state references it directly or only through a saved subagent state, at any depth. A saved subagent state is opaque to the Host, so the closure is how the Host knows which saved values a checkpoint needs. For example, if the parent state references child state A and A references screenshot B, the closure lists both A and B.

- **Default store.** Without a Host store, saved values live in a namespace of Harness state and travel with it. Saving drops values outside the state's reference closure, so each inline child keeps only its latest state, as today. Harness UI and simple embedders need no store of their own.
- **Service store.** The Service saves each value as a compressed object owned by the Run that saves it ([object keys](03-storage-and-apis.md#object-store)) and verifies the digest on load. A value is saved once. Later checkpoints of the same Run, and successor Runs that inherit the state, keep the same reference.

## Large content

Computer-use screenshots and other binary inputs stay inline in message history today ([`computer.py`](../../../packages/a13n-harness/a13n_harness/toolsets/computer.py)), so every boundary uploads all of them again.

When the Harness exports state, each binary content part larger than a threshold (initially 64 KiB) is saved through the store, and the exported message holds a reference with its media type, size, and digest. Parts already saved keep their reference, so a screenshot is uploaded once, not at every boundary. Loading resolves references before the history reaches the model, so the Agent sees the same messages as before.

A tool result's screenshot reaches history before the next model request. The export at that model boundary saves it, and the request waits for that export ([`boundaries.py`](../../../packages/a13n-service/a13n_service/runs/boundaries.py)), so the first upload of a new screenshot delays the next model request once. The boundary still does not wait for its commit, and later checkpoints carry only the reference.

## Inline subagent state

Inline subagents keep executing inside the parent's tool call, in the same worker, with no Thread or Run of their own in the Service. Their events stream into the parent Run's visible items, tagged with `subagentRunId`, and their usage is charged to the parent Run. Only their storage changes ([`delegation.py`](../../../packages/a13n-harness/a13n_harness/toolsets/delegation.py), [`subagents.py`](../../../packages/a13n-harness/a13n_harness/capabilities/subagents.py)).

The parent's `a13n.subagents` namespace becomes a registry of references:

```text
InlineSubagentEntry
  child_instance_id             # for example researcher-1a2b
  subagent_name
  child_definition_id
  child_thread_id
  state: StoredRef              # today: the child's complete HarnessState
  refs: list[StoredRef]         # reference closure of the saved state
```

- **Save.** When a child run ends, completed or failed, the Harness saves its state once through the store and records the entry with that state's reference closure, so the parent's export lists those values without loading the child. A cancelled child is not saved, as today. The saved state omits `a13n.usage` and borrowed environment states.
- **Run start.** Starting a parent Run only reads the registry. It no longer validates every retained child against the current Agent.
- **Resume.** `resume_subagent` loads the referenced state and checks that child's definition. A changed definition fails that call only, and the model can delegate a new child instead.
- **Fork.** Forking the parent gives every entry a new `child_thread_id` without reading or copying stored states. When a stored state is loaded and its Thread ID differs from the entry's, the Harness forks it then.

A child's progress while it runs is not checkpointed, as today. The Harness receives a child's state only when the child ends, and the Service checkpoints only the parent's boundaries. If the worker fails during a delegation, the recovered parent repeats the tool call from its last checkpoint: `delegate` starts a new child, and `resume_subagent` continues from the child's last saved state. A state saved before the failure but never referenced by a committed checkpoint is removed by the seal cleanup.

Asynchronous subagents are already separate Threads and Runs. Their states and visible items follow this proposal as ordinary Runs.

The registry changes the Harness state format for every Host, so [Harness state and resume](../../a13n-harness/10-snapshot-and-resume.md) changes accordingly. With the default store nothing leaves Harness state.

## Continuing after a failed or cancelled Run

Today only completed and waiting Runs become the Thread's head, and the next Run continues from the head ([`seal.py`](../../../packages/a13n-service/a13n_service/runs/seal.py), [`accept.py`](../../../packages/a13n-service/a13n_service/runs/accept.py)). Everything a failed or interrupted Run did is lost to the conversation, however long it ran.

Because every boundary already commits a complete state, a failed or cancelled Run's last checkpoint is a usable baseline. The proposal changes the continuation rule:

- **Head.** Every sealed Run becomes the head, whatever its outcome. A Run that sealed before its first checkpoint resolves to the state it started from. A waiting head still requires resume.
- **Pausing is unchanged.** Automatic advancement still pauses after a failed or cancelled Run; only the baseline that the next explicit message continues from changes.
- **Fork** accepts failed and cancelled Runs and starts from their last checkpoint ([`submit.py`](../../../packages/a13n-service/a13n_service/runs/submit.py)).
- **Unfinished tool calls.** A Run can fail after a tool boundary committed tool calls but before their results were committed. A Run that continues or forks from a failed or cancelled Run passes `tool_recovery="never"`. The Harness's existing recovery then gives each such call a result stating that it was interrupted and that its outcome is unknown, since the tool may have run, and runs none of them again ([`recovery.py`](../../../packages/a13n-harness/a13n_harness/recovery.py)). Takeover attempts and every other Run start keep `declared`, so a takeover still repeats calls declared recoverable.
- **Child results.** A child result whose delegating call is in the origin Run's last checkpoint is delivered as an ordinary queued source. Otherwise it still fails with `origin_not_committed`.
- **Escape hatch.** If the failure was caused by the history itself, such as a provider rejecting it, continuing fails again. The caller can fork from an earlier Run instead.

The Service specification changes accordingly: [Runs](../05-runs.md) (head, fork, child results, and the database check on `head_run_id`) and [facts and delivery](../07-facts-and-delivery.md) (a failed Run's checkpoint is a baseline, not only inspection evidence).
