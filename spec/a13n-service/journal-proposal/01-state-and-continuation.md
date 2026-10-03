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
- **Compression.** State objects are compressed with zstd. The pointer records the format, so readers know how to decode it.

What remains is message text, small Capability state, environment states, and the Service's progress fields. Text compresses well, so a boundary uploads a fraction of the text context.

## The storage binding

The Harness owns its state format, so it decides what is stored separately. A Host provides one storage binding with two operations: save a value and return a reference, and load a value by reference. The Harness uses it for inline subagent states and for large binary content parts. The default binding keeps values inside Harness state, so Harness UI and simple embedders work unchanged.

The Service implements the binding with objects owned by the Run that saves them ([object keys](03-storage-and-apis.md#object-store)). A value is saved once. Later checkpoints of the same Run, and successor Runs that inherit the state, keep the same reference.

## Large content

Computer-use screenshots and other binary inputs stay inline in message history today ([`computer.py`](../../../packages/a13n-harness/a13n_harness/toolsets/computer.py)), so every boundary uploads all of them again.

When the Harness exports state, each binary content part larger than a threshold (initially 64 KiB) is saved through the binding, and the exported message holds a reference with its media type, size, and digest. Parts already saved keep their reference, so a screenshot is uploaded once, not at every boundary. Loading resolves references before the history reaches the model, so the Agent sees the same messages as before.

A tool result's screenshot reaches history before the next model request. That model boundary does not hold the request for its commit, so the upload does not delay the model or the tools.

## Inline subagent state

Inline subagents keep executing inside the parent's tool call. Only their storage changes:

- When a child run ends, the Harness saves its state through the binding and records a registry entry in the parent's `a13n.subagents` namespace: child instance ID, subagent name, definition ID, child Thread ID, and the stored state reference. The saved state omits `a13n.usage`.
- `resume_subagent` loads the referenced state and validates its definition at that point. An incompatible child fails that resume, not every later Run of the parent Thread ([`subagents.py`](../../../packages/a13n-harness/a13n_harness/capabilities/subagents.py)).
- Fork rewrites the Thread IDs in registry entries. A child's stored state is forked when it is next loaded.
- Registry entries always hold references. With the default binding, the referenced states live inside Harness state.

A child's progress while it runs is not checkpointed, as today. If the worker fails during a delegation, the recovered parent repeats the tool call from its last checkpoint: `delegate` starts a new child, and `resume_subagent` continues from the child's last saved state.

Asynchronous subagents are already separate Threads and Runs. Their states and visible items follow this proposal as ordinary Runs.

## Continuing after a failed or cancelled Run

Today only completed and waiting Runs become the Thread's head, and the next Run continues from the head ([`seal.py`](../../../packages/a13n-service/a13n_service/runs/seal.py), [`accept.py`](../../../packages/a13n-service/a13n_service/runs/accept.py)). Everything a failed or interrupted Run did is lost to the conversation, however long it ran.

Because every boundary already commits a complete state, a failed or cancelled Run's last checkpoint is a usable baseline. The proposal changes the continuation rule:

- **Head.** Every sealed Run becomes the head, whatever its outcome. A Run that sealed before its first checkpoint resolves to the state it started from. A waiting head still requires resume.
- **Pausing is unchanged.** Automatic advancement still pauses after a failed or cancelled Run; only the baseline that the next explicit message continues from changes.
- **Fork** accepts failed and cancelled Runs and starts from their last checkpoint ([`submit.py`](../../../packages/a13n-service/a13n_service/runs/submit.py)).
- **Unfinished tool calls.** A Run can fail after a tool boundary committed tool calls but before their results were committed. Before the next Run adds its input, the Harness adds a result for each such call stating that it was interrupted and that its outcome is unknown, since the tool may have run. The existing integrity filter only drops orphan results; it does not add missing ones ([`integrity.py`](../../../packages/a13n-harness/a13n_harness/filters/integrity.py)).
- **Child results.** A child result whose delegating call is in the origin Run's last checkpoint is delivered as an ordinary queued source. Otherwise it still fails with `origin_not_committed`.
- **Escape hatch.** If the failure was caused by the history itself, such as a provider rejecting it, continuing fails again. The caller can fork from an earlier Run instead.

The Service specification changes accordingly: [Runs](../05-runs.md) (head, fork, child results, and the database check on `head_run_id`) and [facts and delivery](../07-facts-and-delivery.md) (a failed Run's checkpoint is a baseline, not only inspection evidence).
