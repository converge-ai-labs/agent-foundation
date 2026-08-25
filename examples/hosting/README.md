# Host Persistence Example

This standalone application demonstrates how an embedding Host persists and resumes the public Agent Harness state boundary without turning `HarnessState` into lifecycle authority. It runs entirely offline with a Pydantic AI `FunctionModel` and stores bounded example records on a local filesystem.

The example intentionally separates:

- the portable continuation value exported by the Harness;
- the Host-owned Execution, ExecutionAttempt, fence, selected checkpoint, and terminal result;
- fresh Identity, policy, model, and Environment bindings reconstructed for every Harness run.

The normative contracts are [Harness State and Resume](../../spec/agent-harness/10-snapshot-and-resume.md), [Harness Hosting Contract](../../spec/agent-harness/13-hosting-contract.md), and [Foundation Durable Execution Lifecycle](../../spec/foundation-service/03-execution-lifecycle.md). This application is teaching code, not an additional public API.

## Run It

From the repository root:

```bash
make examples-check-all
```

Or run only this project:

```bash
cd examples/hosting
uv sync --locked
uv run host-persistence-example
uv run pytest
```

The default command uses a temporary state directory. Retain and inspect the files by choosing an application-owned location:

```bash
uv run host-persistence-example --state-dir ./host-example-state
```

Do not place this directory inside an Agent workspace or disposable Environment. The Host must still be able to select the checkpoint after the Environment, worker, or process that produced it is gone.

## What the Demo Does

```mermaid
sequenceDiagram
    participant Host
    participant Store as Host store
    participant Harness

    Host->>Store: create Execution
    Host->>Store: acquire ExecutionAttempt 1 and opaque fence
    Host->>Harness: run with fresh bindings
    Harness-->>Host: interrupted failure and safe HarnessState candidate
    Note over Harness,Host: partial text retained; unfinished thinking excluded
    Host->>Store: persist checkpoint 1, then select it
    Host->>Store: invalidate ExecutionAttempt 1 without terminal commit
    Host->>Store: acquire ExecutionAttempt 2 with checkpoint 1 and a new fence
    Host->>Store: reject stale ExecutionAttempt 1 checkpoint write
    Host->>Harness: new run with fresh bindings and previous_state
    Harness-->>Host: replacement result and HarnessState candidate
    Host->>Store: persist and select checkpoint 2
    Host->>Store: fenced terminal commit
```

The first model stream emits one finalized thinking block, visible answer text, and then an unfinished thinking block before raising a simulated transport failure. The Harness result is failed, but its continuation candidate retains the answer text and finalized thinking while excluding unfinished thinking. The Host deliberately selects that candidate without committing the failure as the Host terminal result. The replacement ExecutionAttempt creates a new Harness run, receives fresh bindings, and resumes from only the explicitly selected state.

A real Host does not manufacture a recovery prompt blindly after an uncertain external mutation. It first reconciles the owning provider or relies on an operation-specific idempotency contract.

## Storage Layout

`JsonFileHostStore` writes:

```text
<state-dir>/
  executions/
    execution-1/
      execution.json
      checkpoints/
        checkpoint-1.json
        checkpoint-2.json
```

`execution.json` is the local authority pointer. It contains:

| Field                       | Meaning                                                                               |
| --------------------------- | ------------------------------------------------------------------------------------- |
| `definition_revision_ref`   | Exact Host definition revision reconstructed for every ExecutionAttempt               |
| `agent_instance_id`         | Stable, non-authoritative Agent instance identity for the logical Execution           |
| `state` and `version`       | Host lifecycle projection and optimistic transition version                           |
| `current_execution_attempt` | Current generation, ExecutionAttempt ID, starting checkpoint, and opaque-fence digest |
| `selected_checkpoint_ref`   | The only checkpoint selected for later resume                                         |
| `terminal_output`           | Host-committed terminal projection, independent of Harness completion                 |

Each immutable checkpoint contains:

| Field                                          | Meaning                                                                         |
| ---------------------------------------------- | ------------------------------------------------------------------------------- |
| Execution/ExecutionAttempt/generation/sequence | Producing provenance checked by the Host                                        |
| `thread_id`                                    | Stable Thread correlation matching `harness_state.thread_id`                    |
| `harness_run_id`                               | Correlation with the process-local Run that exported the candidate              |
| `harness_state`                                | Detached portable continuation value                                            |
| `effective_environment_topology_ref`           | Optional Host evidence for the topology actually published by that run          |
| `launch_state_ref`                             | Optional reference to separately protected provider launch or reattachment data |

The example has no remote provider and therefore leaves both optional references empty. It does not invent a generic launch-state payload: each Host/provider integration owns that schema, protection, compatibility, and reconstruction logic.

## The Harness Payload

The Host serializes the public model directly:

```python
payload = harness_state.model_dump_json()
restored = HarnessState.model_validate_json(payload)
```

`HarnessState` contains only:

- one stable `thread_id` for the independently advancing history;
- public Pydantic AI message history;
- versioned JSON values owned by stable Capability IDs;
- optional portable Environment backend state for already selected compatible bindings.

It deliberately excludes:

- definition source or revision selection;
- Agent Identity authority, actor authentication, policy, grants, or credentials;
- Model, Toolset, Capability, plugin, provider client, or Python callable objects;
- desired Environment topology, live bindings, controllers, sandbox reachability, or launch authority;
- Execution state, ExecutionAttempt generation, lease fence, queue, pending delivery, or terminal commit;
- durable task maps, long-term memory records, artifacts, lifecycle events, or usage accounting.

The tests inspect the stored checkpoint and verify that Execution, ExecutionAttempt, definition, Agent instance, and fence facts did not leak into the nested Harness payload.

## Fresh Bindings on Resume

`application.py` reconstructs a new `RunBindings` object for each ExecutionAttempt. It deliberately preserves the stable Host-selected Agent instance ID while replacing the process-local binding and Environment objects:

```python
bindings = RunBindings(
    instance=AgentInstanceContext(
        identity=current_identity,
        agent_instance_id=record.agent_instance_id,
        host_refs={
            "execution": record.execution_id,
            "execution_attempt": lease.execution_attempt_id,
        },
    ),
    environment=materialize_current_environment(),
)
```

A production Host performs current authentication, policy evaluation, credential resolution, model selection, desired-topology resolution, and provider materialization before it calls the Harness. Saved messages and Capability or Environment data can restore continuation, but they never restore authority.

Environment restore follows this order:

1. the Host resolves current desired topology and protected provider launch state;
2. the Host materializes fresh unentered `EnvironmentRunBinding` values;
3. the Harness enters the already selected bindings;
4. the Harness restores compatible portable `environment_state` data;
5. the paired controller activates for that logical run.

Portable state cannot create a binding, select a provider, attach a sandbox, or grant access.

## Selection and Fencing

The local store demonstrates three important properties:

1. **Payload before authority:** it atomically writes an immutable checkpoint file before atomically replacing the `selected_checkpoint_ref`. On POSIX filesystems that honor file and directory `fsync` plus atomic rename, a crash between the writes leaves an unselected candidate rather than a pointer to missing bytes.
2. **Fresh ExecutionAttempt fence and starting point:** ExecutionAttempt acquisition freezes the currently selected checkpoint in the ExecutionAttempt record and returns that exact immutable payload with a new high-entropy opaque fence. The store persists only the fence digest and rejects a stale or mismatched lease before selecting later state or committing completion.
3. **Terminal separation:** a valid Harness result is still only a candidate until the Host performs its own fenced terminal transition.

`JsonFileHostStore` deliberately supports one live store instance for one root in one event loop. Its `asyncio.Lock` is instance-local; multiple instances, event loops, threads, or processes targeting the same root are outside this teaching implementation. Cancellation does not release that lock until an in-flight local write has joined. The helper persists file and directory metadata on POSIX; on other platforms it demonstrates process-local write ordering and atomic replacement rather than claiming power-loss durability. A production Host must make current-ExecutionAttempt validation, starting-checkpoint selection, lifecycle mutation, and its matching durable event one transactional authority operation. Queue or Redis ownership is not a substitute.

The local JSON layout is not prescribed by the Harness or Foundation architecture. An adopter may keep bounded state inline in its authority store or place immutable bytes in an object store and select a verified reference. The observable requirements are ownership, fencing, complete-boundary selection, compatibility, and recoverability—not a particular database or blob technology.

## Interrupted Output and Complete Boundaries

Interrupted model output is recovered on a best-effort basis before it becomes continuation state:

- visible text already emitted is retained when it is safe to continue from it;
- completed thinking may be retained with its integrity data;
- unfinished thinking is excluded from replay;
- only complete tool calls are eligible for interrupted-history repair;
- response parts without a safe partial form are not reconstructed.

This is a Harness responsibility, not a Host-specific message filter. Every Host receives the same safe `HarnessState` candidate. The Host persists state from a delivered terminal result in this demo. During streaming it may also call `HarnessRunStream.export_state()` while the stream context is active; before interruption that method sees only the latest safe public message boundary. It never persists raw token deltas or performs storage I/O.

Live stream events are observations and cannot be retracted. A consumer may have displayed an unfinished thinking delta before the failure, but that delta is not part of the selected continuation and must not be replayed after resume. Product display history and durable model continuation are separate projections.

A Host must not select:

- arbitrary raw model deltas instead of the Harness-produced safe state;
- a state export that failed or exceeded its bounds;
- a candidate from a stale ExecutionAttempt;
- a checkpoint whose referenced Host launch state is missing or incompatible;
- an observation that has not incorporated an accepted input it claims to contain.

`RunCleanupError.outcome` remains an uncertain candidate because clean Harness terminal delivery did not occur. Host policy decides whether it can be selected or requires reconciliation; it must not be reported automatically as clean completion.

## External Effects and Other Durable State

A checkpoint records observations; it does not make a tool or provider mutation exactly once. If execution stops after dispatch but before an authoritative result, the outcome remains unknown. The Host or provider must inspect current state, use the same idempotency key, or enter an explicit reconciliation path before replay.

Keep these records outside `HarnessState`:

| Concern                                         | Durable owner                  |
| ----------------------------------------------- | ------------------------------ |
| Session, Turn, Item, and display history        | Product or Host projection     |
| Definition revisions and artifact locks         | Host definition control        |
| Desired topology and provider launch state      | Host/provider integration      |
| Client-side pending calls and feedback receipts | Host delivery lifecycle        |
| Asynchronous children and result routing        | Host child Execution lifecycle |
| Cross-worker tasks                              | Durable task provider          |
| Long-term memories                              | Memory provider or Host        |
| Lifecycle events and replay cursors             | Host lifecycle authority       |
| Cross-run usage and billing                     | Host accounting system         |

## Adapting the Example

An embedded application can replace only `JsonFileHostStore` and keep the same Harness call path. A distributed service additionally needs:

- durable acceptance before scheduling;
- one current fenced ExecutionAttempt generation;
- immutable definition and provider integration revision selection;
- authoritative checkpoint selection and terminal compare-and-swap;
- explicit pending-delivery and side-effect reconciliation state;
- retention, encryption, tenant isolation, and codec migration policies;
- durable lifecycle events independent from live stream delivery.

Do not put database sessions, object-store clients, worker leases, or provider credentials into `RunBindings.metadata` or `HarnessState`. Keep them behind the Host application and narrowly typed run collaborators.
