---
title: Lifecycle and state
description: Manage targets separately from reusable connectors and independent executions.
---

The Host manages a target and saves the result before passing a fixed-target connector to Harness. The independent `a13n-environment` package also works directly in ordinary applications.

## Three objects

| Object                 | Responsibility                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `EnvironmentProvider`  | Account-scoped management client for explicit create, start, inspect, stop, keepalive, and destroy                  |
| `EnvironmentConnector` | Reusable configuration for a fixed target; construction acquires no live client                                     |
| `EnvironmentExecution` | A ready execution returned by `await connector.open()`, with its own clients, operation handles, and `execution_id` |

A connector can serve several Runs. Every `open()` creates a separate execution. Opening, readiness checks, and execution cleanup never create, start, replace, or renew the target. A lost saved target fails instead of selecting another resource with the same name.

`close()` releases only resources owned by that object. Native process survival follows the Provider contract: E2B disconnects observations, while Direct Local cleans up processes it started. Target retention and destruction remain Host decisions.

## Manage and publish before execution

In this fragment, the Host supplies state storage, concurrency control, and cancellation protection:

```python
from a13n_environment.docker.provider import DOCKER
from a13n_environment.errors import observed_environment_state

recipe = {"image": "ghcr.io/converge-ai-labs/a13n-sandbox:dev"}
state = await state_store.load(environment_key)
async with await DOCKER.open_provider(configuration=account_configuration) as provider:
    try:
        if state is None:
            state = await provider.create(recipe, environment_id="env-workspace", operation_id="op-create")
        else:
            state = await provider.start(recipe, environment_id="env-workspace", state=state, operation_id="op-start")
    except BaseException as error:
        observed = observed_environment_state(error, state)
        await state_store.publish(environment_key, observed)
        raise
    await state_store.publish(environment_key, state)
    connector = provider.execution_connector(recipe, environment_id="env-workspace", state=state)

result = await executable.run("Continue the task", environment=connector)
```

`EnvironmentState` retains its existing credential-free format. It contains no live client, execution handle, Harness mount policy, or destruction authority. Management methods return the current reference; `inspect()` returns both status and reference.

Management can fail after partial success. `EnvironmentManagementError` and `EnvironmentManagementCancelled` preserve the reference known at failure. `observed_environment_state(error, previous)` also follows exception chains to find that observation. A confirmed cleared `None` differs from having no new observation. Publish under the Host's conditional update and cancellation policy before releasing management ownership.

A connector's `state` is a detached fixed reference. Execution only validates and uses that target; it produces no new management state to publish after a Run. `HarnessState.environment_states` aggregates mount state and does not replace the Host's authoritative management record.

## Explicit destruction

```python
async with await definition.open_provider(
    configuration=account_configuration, credential=current_credential
) as provider:
    try:
        await provider.destroy(recipe, environment_id="env-workspace", state=state, operation_id="op-destroy")
    except BaseException as error:
        await state_store.publish(environment_key, observed_environment_state(error, state))
        raise
    await state_store.publish(environment_key, None)
```

Clear state only after confirmed destruction. Unknown outcomes retain the known reference for later inspection. Destruction addresses the validated target and Provider-owned material; execution close never deletes shared Host directories, bind sources, or external volumes.

## Management and execution methods

| Method                                                                      | Effect                                                                    |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `definition.open_provider(...)`                                             | Acquire account management clients without selecting or changing a target |
| `provider.create(recipe, ..., operation_id=...)`                            | Explicitly create or reconcile a managed target                           |
| `provider.start(recipe, ..., state=..., operation_id=...)`                  | Explicitly start an existing target; never recreate a lost target         |
| `provider.inspect(recipe, ..., state=...)`                                  | Read-only `running`, `stopped`, or `absent` status plus reference         |
| `provider.stop(...)` / `destroy(...)`                                       | Explicit lifecycle mutation when supported                                |
| `provider.keepalive(...)`                                                   | Explicit renewal scheduled by the Host                                    |
| `definition.execution_connector(...)` / `provider.execution_connector(...)` | Pure construction of fixed-target connection inputs                       |
| `connector.open()`                                                          | Open a new ready execution                                                |
| `execution.check_ready(families)`                                           | Read-only check of the current execution without target recovery          |
| `execution.close()`                                                         | Idempotent cleanup of this execution's resources                          |

Closing a management client leaves its previously generated connectors usable. The Host closes borrowed runtimes; account configuration may accompany a borrowed runtime, but credentials may not. Local Envd requires a Host runtime. HTTP and WebSocket Envd expose connection only, with no target management.

## Handle failures

Management and Provider boundaries use `EnvironmentProviderError`; operations use `EnvironmentError`. Present `safe_projection()` to users and retain full exceptions for local diagnostics.

| Certainty        | Host response                                                      |
| ---------------- | ------------------------------------------------------------------ |
| `not_dispatched` | Fix input or runtime conditions before considering another attempt |
| `known`          | Act on the known outcome; this does not imply retryability         |
| `unknown`        | Inspect the original operation and target before any replay        |

An unavailable control plane does not mean an absent target. Reconnection requires an explicit new execution, or explicit mount replacement in Harness. Process, output, and computer references from an old execution cannot be used in the new one.

See the [Provider comparison](providers.md#reconnection-and-lifecycle) for cloud-specific stop, filesystem, and expiry rules.
