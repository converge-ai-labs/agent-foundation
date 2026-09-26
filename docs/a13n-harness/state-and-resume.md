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
from a13n_harness import HarnessState

initial_state = HarnessState.new(thread_id="thr_productthread1")
first = await executable.run(
    "Draft the plan",
    bindings=fresh_bindings(),
    previous_state=initial_state,
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

Use `fork()` when copying continuation data into an independently advancing history. Omit the ID for Harness generation or pass a distinct Host-selected ID:

```python
branch_state = state.fork(thread_id="thr_productbranch1")

assert branch_state.thread_id != state.thread_id
assert branch_state.message_history == state.message_history
```

Do not edit serialized state manually. Use `HarnessState.new(thread_id=...)` for an explicit initial identity and `state.fork(thread_id=...)` to change identity while preserving portable continuation data. A fork clears portable Environment state by design. The Harness uses the ID to correlate one history and model-session affinity without treating it as authority.

## Export While Streaming

An active stream can export the latest safe public boundary:

```python
async with executable.stream("Work", bindings=fresh_bindings()) as stream:
    async for item in stream:
        checkpoint_candidate = await stream.export_state()
```

`export_state()` does not persist anything and does not expose arbitrary token deltas. The caller decides whether a candidate is complete, current, and safe to select. The terminal result remains the simplest complete checkpoint boundary.

## Resume Unanswered Tool Calls

A checkpoint can contain tool calls whose results were never recorded. After a crash, those operations may have run even though their results are missing. Supply any [accepted deferred input](#recover-an-interrupted-deferred-resume) retained by the Host before treating a call as unknown: external results, failures, and explicit denials remain authoritative under every recovery mode. For the remaining unresolved calls, `run()` and `stream()` use the per-run `tool_recovery` option to decide which may execute again:

| Mode                   | Unanswered restored calls                                                                    |
| ---------------------- | -------------------------------------------------------------------------------------------- |
| `"declared"` (default) | Execute tools explicitly declared recovery-retryable; fill other calls with unknown results. |
| `"never"`              | Fill every unanswered call with an unknown-result `ToolReturnPart`.                          |
| `"always"`             | Execute every unanswered call still available in the current tool surface.                   |

Recorded tool returns and argument retry results remain authoritative. Recovery preserves the original call IDs and any partial results, including when the saved frontier is interrupted. A tool no longer available receives an unknown result. An existing unknown-result return is already a result and is never reopened, even with `"always"`. Newly generated calls execute normally.

### Declare Retryable Tools at Their Source

Use `recovery_retryable()` as a decorator or wrap an existing function or native `Tool`:

```python
from a13n_harness.tools import recovery_retryable
from pydantic_ai import Tool
from pydantic_ai.capabilities import Capability

@recovery_retryable
def lookup_record(record_id: str) -> str:
    return read_record(record_id)

catalog = Capability(
    id="acme.catalog",
    tools=[lookup_record, recovery_retryable(Tool(search_records, name="search"))],
)
```

The helper returns a native `Tool`. It adds metadata without wrapping execution; an existing `Tool` is copied with its settings and other metadata preserved. For instance methods, call `recovery_retryable(self.lookup_record)` when constructing the Capability. A Plugin can declare its own individual tools this way without requiring the Host to know their names.

Dynamic Toolsets can use the same helper while building their current tools. For integrations that already produce `ToolDefinition` metadata, set `RECOVERY_RETRY_SAFE_METADATA_KEY` from `a13n_harness.tools` to the boolean `True`. Native `SetToolMetadata` also works. The declaration is read from the freshly prepared definition, not from saved messages.

The declaration asserts that repeating the operation is acceptable despite an unknown earlier outcome. It is separate from provider dispatch retries and `HarnessToolMetadata.idempotency`: a provider key alone does not prove a restored call will reuse the original upstream operation.

### Resume with a Policy

```python
result = await executable.run(
    bindings=fresh_bindings(),
    previous_state=checkpoint,
    tool_recovery="declared",  # The default; use "never" to disable all replay.
)
```

You may also supply new input. Native Pydantic AI continuation processes the retained calls and partial results before the next model request. Recovery uses native `ToolApproved` values as programmatic permission to replay selected calls. Native argument validation still runs, tools currently requiring approval or external execution still suspend, and managed invocations are checked against fresh policy and resources. Recovery replay permission does not satisfy current approval requirements. A fresh `DeferredToolResume` uses the supplied approval/result batch independently of `tool_recovery`; an interrupted resume uses `recovery=True` and the current recovery policy. Provider-suspended responses retain their native continuation path.

This option belongs to the run, not serialized state or model retry policy. It does not guarantee exactly-once effects or stop a later model decision from requesting another call.

The former `execute_pending_tools` argument is replaced by `tool_recovery`: migrate `False` to `"never"`, `True` to `"always"`, and `"auto"` to `"declared"`. The default now follows per-tool declarations; unmarked tools continue to receive unknown results.

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

The exact result-building API depends on whether the deferred item is an approval, external call, or structured user question. Cover every pending item exactly once and preserve its category. The [client-side tool example](client-tools.md) demonstrates a complete offline declaration, suspension, correlated external result, and resume. [Managed tool policy](managed-tools.md) explains approval metadata and fresh authorization.

Resume validates that:

- previous state is present;
- the deferred request and supplied results correlate exactly;
- pending calls match the legal message history and current tool category;
- overridden arguments validate again;
- fresh policy and provider enforcement still allow dispatch.

A prior approval does not bypass current policy. Hosts may construct legal history and supply native `bool`, `ToolApproved`, or `ToolDenied` decisions without private Harness metadata. Historical argument/schema fingerprints, resource revisions, and managed identity attestations are not required. Keep pending requests consistent with history; use `ToolApproved.override_args` for replacement arguments. Current schema validation, review, policy and Environment enforcement still apply. Approval does not reserve the backing target or authorize automatic replay after an unknown outcome.

This native resume flow also applies to Host-managed children when current bindings support deferred tools. [Built-in inline children](delegation-and-codeact.md#host-managed-feedback) disable deferred tools and support ordinary prompt continuation only. Hosts without a deferred lifecycle explicitly set `deferred_tools_supported=False`; lineage alone does not disable interaction.

## Recover an Interrupted Deferred Resume

An external result can be accepted before native history records it. For example, a batch may contain both an external result and a local approved action, and execution can stop at a checkpoint before that action runs. The intermediate `HarnessState` alone is not a complete record of the accepted batch. Harness does not keep a second deferred-result representation inside Capability state.

The Host retains the correlated native `DeferredToolRequests` and `DeferredToolResults` alongside the selected checkpoint, or in its existing durable input records, until history incorporates them. When publishing a new checkpoint, `accepted.remaining(checkpoint.message_history)` returns the unincorporated batch, or `None` when it has been incorporated or superseded by a later response. Persist this value with the checkpoint under the Host's normal publication rules; receiving input or calling `export_state()` alone does not save it.

For recovery from an interrupted resumed Run, provide the retained batch with `recovery=True`:

```python
from a13n_harness import DeferredToolResume

# Loaded from the Host's checkpoint and accepted-input storage.
recovery_input = (
    DeferredToolResume(accepted.requests, accepted.results, recovery=True)
    if accepted is not None
    else None
)
result = await executable.run(
    bindings=fresh_bindings(),
    previous_state=checkpoint,
    deferred_resume=recovery_input,
    tool_recovery="declared",
)
```

Recovery filters out results already in history, validates the remaining correlation and current tool surface, and consumes retained external facts and explicit denials even with `tool_recovery="never"`. It never reuses a positive approval as permission to replay. Unresolved local work follows the current recovery policy and requires fresh approval where applicable. This also applies to Host-managed child checkpoints; it does not create an exactly-once side-effect guarantee or recover input the Host never saved.

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

## Run-local Shell Observations

`DynamicEnvironmentCapability` composes shell execution, `shell_info` discovery/inspection, explicit-offset `shell_wait`, and supported stdin/control tools from each mount's actual actions. A reference belongs to one Harness Run, not to a durable process service. Queries do not reset output, and native completion does not imply complete capture.

Run close releases observations without blanket process termination. A fresh adapter restored from Provider state may discover commands the backend retained; a fresh Run assigns new references and cannot use old references from history. No process reference, buffer, watcher or output cursor enters Harness Capability state. Provider state owns native recovery evidence, and the Host owns target lifetime and state publication.

See [Environment tools](environments.md) for usage and Provider-specific limitations. No Host process manager, process database, or durable post-Run wake integration is required.

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
