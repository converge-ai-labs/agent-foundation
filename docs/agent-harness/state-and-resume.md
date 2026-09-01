# State and Resume

`HarnessState` is the portable continuation value for one independently advancing Thread. It preserves model history, versioned Capability namespaces, and optional portable Environment data. It deliberately does not preserve authority or Host lifecycle state.

## State Contents

A state envelope contains:

- `schema_version`;
- a stable `thread_id`;
- public Pydantic AI message history;
- detached, versioned JSON values owned by Capability IDs;
- optional provider-defined portable Environment state for already selected compatible mounts.

It excludes:

- Models, Toolsets, Capabilities, plugins, callables, or live clients;
- identity authentication, policy, grants, credentials, or approvals;
- desired Environment mount definitions, runtime mutation authority, provider sessions, or launch state;
- Host execution records, attempts, leases, queues, or terminal commits;
- durable asynchronous children, long-term memory, delivery, billing, or accounting state.

State restores data, not authority.

## Continue a Thread

A completed, suspended, or safely failed result may carry a state candidate:

```python
first = await executable.run(
    "Draft the plan",
    bindings=fresh_bindings(),
)

if first.state is None:
    raise RuntimeError("No continuation state is available")

second = await executable.run(
    "Review it",
    bindings=fresh_bindings(),
    previous_state=first.state,
)
```

`first.thread_id == second.thread_id`, while `first.run_id != second.run_id`. The new run reconstructs current bindings and creates a fresh context, `EnvironmentRuntime`, plugin graph, and usage accumulator.

## Serialize State

`HarnessState` is a frozen Pydantic model:

```python
from a13n_harness import HarnessState

payload = state.model_dump_json()
restored = HarnessState.model_validate_json(payload)
```

Persist the complete validated envelope, not private encoded fields or raw model deltas. A Host should associate it with its own definition revision, provider lifecycle state, checkpoint provenance, and any Host-owned validation metadata outside the Harness payload.

## Fork a Thread

Use `fork()` when copying continuation data into an independently advancing history:

```python
branch_state = state.fork()

assert branch_state.thread_id != state.thread_id
assert branch_state.message_history == state.message_history
```

Do not change a serialized `thread_id` manually. The Harness uses it to correlate one history and model-session affinity without treating it as authority.

## Export While Streaming

An active stream can export the latest safe public boundary:

```python
async with executable.stream("Work", bindings=fresh_bindings()) as stream:
    async for item in stream:
        checkpoint_candidate = await stream.export_state()
```

`export_state()` does not persist anything and does not expose arbitrary token deltas. The caller decides whether a candidate is complete, current, and safe to select. The terminal result remains the simplest complete checkpoint boundary.

## Structured Suspension

Native deferred tools and approvals end a root logical run with `status="suspended"`. The result includes:

- `state`, representing the accepted history and Capability data;
- `deferred`, the exact native pending request envelope;
- `suspend_reason="deferred"`.

The application performs external interaction after the run is closed, then starts a new run:

```python
from a13n_harness import DeferredToolResume

first = await executable.run(
    "Ask for confirmation",
    bindings=fresh_bindings(),
)

if first.status != "suspended" or first.state is None or first.deferred is None:
    raise RuntimeError("Expected a suspended run")

results = first.deferred.build_results(approve_all=True)

second = await executable.run(
    bindings=fresh_bindings(),
    previous_state=first.state,
    deferred_resume=DeferredToolResume(first.deferred, results),
)
```

The exact result-building API depends on whether the deferred item is an approval, external call, or structured user question. Cover every pending item exactly once and preserve its category.

Resume validates that:

- previous state is present;
- the deferred request and supplied results correlate exactly;
- managed tools remount with the expected identity and schema;
- overridden arguments validate again;
- fresh policy and provider enforcement still allow dispatch.

A prior approval does not bypass current policy.

This resume flow is root-only. Child invocations remove declaratively deferred tools and convert dynamic deferral into `ToolDenied` results while continuing the same run. A Host must not persist or submit `DeferredToolResume` for a child.

## Safe Failure Candidates

A failed result can include a safe state candidate when the Harness can normalize the interrupted history. For example, it can retain complete visible text and completed thinking while excluding an unfinished thinking part.

A candidate is not a durable recovery decision. Before selecting it, a Host must consider whether an external mutation may have been dispatched without an authoritative result. Reconcile the provider or reuse an operation-specific idempotency contract before replaying uncertain work.

`RunCleanupError.outcome` is also only an uncertain candidate because clean terminal delivery did not occur.

## Capability State

A stateful Capability uses one stable namespace and exact version:

```python
from pydantic import BaseModel


class CounterState(BaseModel):
    value: int


current = await context.state.read(
    "acme.counter",
    CounterState,
    version="1",
)
await context.state.write(
    "acme.counter",
    CounterState(value=(current.value if current else 0) + 1),
    version="1",
)
```

Unknown namespaces can remain opaque across a run. Only the owning Capability interprets its payload and version. Do not store credentials, clients, locks, or `EnvironmentState` in a Capability namespace.

## Run-owned Shell Processes

Shell process state is deliberately absent from `HarnessState`. A process-capable `shell_exec` can return a concise `process-*` reference, but that reference addresses only the private controller for the exact logical Run that started it. Harness does not persist the reference, sequence, bound handle, output offsets, status mirror, watcher, or cleanup state in a Capability namespace.

Within the same Run, `shell_wait` uses caller-supplied stdout and stderr offsets and performs non-consuming retained-output reads. Harness stores no unread cursor. When the Run closes, it kills every still-live owned process and releases its handles before Environment adapters close.

A continuation or fork starts with a fresh empty process controller and a new reference incarnation. A process reference retained in message history is historical text, not authority, and cannot be rebound from a mount, Provider process ID, or `EnvironmentState`. Cross-Run or restart-surviving process execution belongs to a separate Host capability outside the current Harness shell contract. See [Embedding in a Host](hosting.md#run-owned-shell-processes).

## Host Checkpointing

A durable Host should keep these facts separate:

| Fact                                                           | Owner                     |
| -------------------------------------------------------------- | ------------------------- |
| Portable conversation continuation                             | `HarnessState` candidate  |
| Selected checkpoint and provenance                             | Host                      |
| Definition revision and artifact lock                          | Host                      |
| Current identity, policy, and credentials                      | Fresh Host reconstruction |
| Desired mount definitions and authoritative `EnvironmentState` | Host/Provider integration |
| Execution attempt, generation, fence, and lease                | Host                      |
| Durable completion and output delivery                         | Host/product              |

See [Embedding in a Host](hosting.md) for the full authority boundary. The runnable [Agent Application example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app) demonstrates the smaller single-application case: stream a turn, commit its returned state, reconstruct the application, and continue the same Thread.
