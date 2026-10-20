---
title: Embedding in a Host
description: Add definition selection, policy, checkpoints, and delivery around the process-local Harness API.
---

An embedded application calls Harness directly. A durable Host adds definition selection, current policy, checkpoint storage, and delivery around the same process-local API. Start with the [offline application example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app) before adding a worker or database.

## Core Integration Path

```mermaid
sequenceDiagram
    participant Client
    participant Host
    participant Harness
    Client->>Host: submit input
    Host->>Host: select definition and checkpoint
    Host->>Harness: build or reuse executable
    Host->>Harness: run(input, bindings, previous_state, environment)
    Harness-->>Host: events and terminal result
    Host->>Host: accept and persist result
    Host-->>Client: deliver outcome

    %% class Client,Host app
    %% class Harness a13n
```

Harness executes one Run; the Host decides which result becomes durable.

## Reconstruct Trusted Definitions

Store a Host-owned schema for model selection, output type, tools, Capabilities, plugins, and child topology. Trusted code resolves those choices into Python objects before calling `HarnessBuilder`; do not deserialize a Python callable or plugin from untrusted input. Harness UI YAML is one Host format, not a universal SDK schema.

For large tool catalogs, a Host can assemble `ToolSearchCapability(groups=...)` from selected sources. See [ToolSearch Host integration](tool-search.md#host-integration).

## Build and Reuse

Cache an executable by its exact trusted definition revision, then supply current inputs to each Run:

```python
result = await executable.run(
    input_value,
    bindings=reconstruct_current_bindings(execution_attempt),
    previous_state=selected_checkpoint,
)
```

Scope streams with `async with executable.stream(...)`. The executable has no `close()` method; close Host-owned clients at their own lifespan boundary.

## Retain Tools Across Conversation Turns

Add `ToolExecutionCapability` to the definition and hold one `ToolExecutionRuntime` for the Host lifetime. Bind the runtime to each Run; use a separate `CodeActStateOwner` per Thread when CodeAct writes must remain visible after the originating Run ends.

```python
from a13n_harness import (
    CodeActStateOwner,
    RunBindings,
    ToolExecutionConfig,
    ToolExecutionRuntime,
)
from a13n_harness.capabilities import ToolExecutionCapability

capability = ToolExecutionCapability(
    ToolExecutionConfig(mode="auto", foreground_wait_seconds=1.0)
)
runtime = ToolExecutionRuntime()
thread_values = CodeActStateOwner()
bindings = RunBindings.embedded(tool_execution=runtime, codeact_state=thread_values)
```

Include `capability` in `AgentDefinition.capabilities` and pass `bindings` to the Run. Fast tools return their native results; eligible slow tools return an execution receipt while the original invocation continues. The default mode is `inline`. Whole CodeAct runners can detach; their nested calls still await real values. Sequential tools retain their execution barrier until the real work settles.

Automatic background execution and immediate steering both require a Host-supplied `RunBindings.tool_execution`. Harness supplies the runtime and its default memory store; Hosts instantiate and hold them. A store alone cannot own live execution. Without the runtime, blocking tools and ordinary queued steering remain available. `auto` rejects a missing binding during Run preparation; immediate steering rejects it before accepting input or preempting, even during model-only generation.

With that binding, `await stream.steer(message, immediate=True)` preempts the current native attempt while keeping the logical Run and supported tool work alive. It also works in `inline` mode. In contrast, `auto` releases the foreground wait without new user input so the Agent can continue. Ordinary steering defaults to `immediate=False`. Accepted pending input is preserved through normal state export; this does not force a checkpoint per message.

The Agent can inspect, retrieve, wait for and cancel work through `tool_execution_info`, `tool_execution_result`, `tool_execution_wait` and `tool_execution_cancel`. Reads are Thread-scoped. Result delivery is acknowledged only after the real result enters canonical history. The default memory store bounds completed retention and is lost on restart. A custom `ToolExecutionStore` persists facts and results, not resumable Python tasks.

Use the runtime's `on_completed` callback to schedule Host delivery without blocking execution. Before closing shared clients, stop new Host admission, settle interactive waiters, then call `await runtime.close(timeout_seconds=...)` and `await runtime.join()`. `close` bounds its cancellation wait; `join` preserves ownership until cooperative cleanup finishes. Conversation cancellation and tool cancellation are separate policies.

## Fresh Authority

On every Run, select the current user/policy, model credentials, Provider state, and current Environment sources. Saved `HarnessState` restores the Thread's message history and Capability state, not clients or permissions. `RunBindings` supplies current collaborators, including optional `model_call_check`; see [Agents and Runs](agents-and-runs.md) and [Environments](environments.md).

### Check Model Calls Before Dispatch

A Host may supply `RunBindings.model_call_check` implementing `ModelCallCheck.check(ModelCall)`. Harness invokes it before a public model call; return to permit the call or raise to refuse it. This is a per-call admission check, not a record that an HTTP request or billable usage occurred. The check is fresh Run authority and is never serialized into `HarnessState`. See [usage correlation](usage-and-limits.md#usage).

## Durable State Boundary

Persist the returned `HarnessState` alongside your Host's definition revision, run/attempt identity, selected Environment state, pending deferred calls, and delivery status. For distributed workers, also keep lease/fence and checkpoint provenance. None of those Host records is recreated from `HarnessState`.

## Attempts and Recovery

When `ModelRecoveryPolicy` is enabled, Harness may retry an interrupted model attempt within one Run; those model attempts share its `run_id`. A replacement worker attempt is a **new** Run with a fresh `run_id`, current bindings, current Environment sources, and the Host-selected checkpoint. If a previous tool mutation has an uncertain outcome, reconcile it before replaying. See [State and Resume](state-and-resume.md).

## Events and Streaming

Consume `HarnessRunStream` within its async scope and project its public events to the application stream or store. A terminal `HarnessRunResultEvent` follows Harness cleanup; persist the result before promising durable completion or delivery to a client.

## Deferred Work

A suspended Run is closed. Store its pending request and checkpoint, authenticate the external result, then start a new Run with `DeferredToolResume`. Retain accepted results when a resume is interrupted before they enter history. [Deferred resume](state-and-resume.md#resume-unanswered-tool-calls) covers the request/result contract.

`RunBindings.deferred_tools_supported` defaults to `True`, so async children can suspend unless the Host disables it. A Host that supports async child deferrals retains the child checkpoint and pending requests. A Host without that lifecycle sets `deferred_tools_supported=False` for each async child Run; ordinary prompt continuation does not require deferred support.

## Environments

Supply a Host-owned `EnvironmentSource` for each target through `environment=` or a named `environments=` mapping. Harness prepares each target on first use, opens a fresh execution, and closes it without destroying the target; the Host owns Provider configuration, authoritative `EnvironmentState`, retention, and explicit `destroy()`. For multiple mounts, choose `default_environment` explicitly when a default route is needed. [Environments](environments.md) has runnable examples.

## Run-local Shell Observations

Harness issues process references only within the current Run and releases the Run's process observations when the Run closes; it does **not** kill every backend command. A later Run constructs a new adapter and uses Provider state/native discovery if the backend retained the process. Do not persist `process-*` references as a recovery mechanism.

## Minimal vs. Production Host

A single-process application can use embedded bindings and persist only the `HarnessState` of successful Runs. Add durable acceptance, fencing, separate Environment state, pending-call records, and delivery tracking when multiple workers or deferred actions require them. These are Host workflows, not extra Harness loop APIs.

## Runnable Example

The [Agent Application example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app) streams turns, atomically stores successful `HarnessState`, and constructs a fresh Direct Local Environment on each call. [Provider plugin examples](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) show installed Provider selection and Environment Run extensions.
