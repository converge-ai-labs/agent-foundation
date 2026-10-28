---
title: Lifecycle and state
description: Prepare a target, save its reference, reuse it across Runs, and destroy it explicitly.
---

To reuse a workspace across Runs, manage the target separately from each execution. The Host creates or starts the target and saves its reference. Harness opens an execution against that target and closes the execution when the Run ends.

## Three objects

| Object                 | Use it to                                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------------------------- |
| `EnvironmentProvider`  | Manage targets through an account-scoped Service client: create, start, inspect, stop, renew, or destroy      |
| `EnvironmentConnector` | Describe one fixed target for repeated use, without acquiring a live client at construction                   |
| `EnvironmentExecution` | Access supported operations after `await connector.open()`, with its own clients, handles, and `execution_id` |

Reuse a connector across Runs; open a separate execution for each owner. Opening or checking an execution does not start, replace, or renew its target. If the saved target is lost, return to Host lifecycle handling rather than selecting another resource with the same name.

Execution close releases resources according to the backend. Native Docker and E2B disconnect observations, Direct Local cleans up owned processes, and Envd cleans up Session resources. Closing a management client releases that client. Neither close operation destroys the target.

## Manage and publish before execution

For a managed target, use this sequence:

1. Claim the lifecycle operation under the Host's concurrency policy and load current state.
2. Create a target if none is selected, or start the retained target.
3. Publish the resulting state, including state observed before a failure.
4. Build a fixed-target connector from the published state and hand it to the Run.

The following async integration fragment requires `a13n-service`, Docker account settings, your `state_store`, and an `executable`. `PreparedSource` is the Host class from the [Harness environment guide](../a13n-harness/environments.md#supply-a-source). Supply concurrency control around the load/manage/publish sequence and cancellation-safe publication in the Host. Keep vendor I/O outside database transactions. The shown operation IDs are illustrative; preserve your actual lifecycle operation's identity through reconciliation.

```python
from a13n_service.providers.environments.docker.definition import DOCKER
from a13n_service.providers.environment.errors import observed_environment_state

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
    connector = DOCKER.execution.execution_connector(
        DOCKER.execution_environment(recipe), configuration=account_configuration,
        environment_id="env-workspace", state=state
    )

result = await executable.run("Continue the task", environment=PreparedSource(connector))
```

After publication, the Host record identifies the target to reuse. `EnvironmentState` holds that credential-free reference. It carries neither live clients nor mount permissions or destruction authority. The connector keeps a detached copy; execution does not produce management state to save after the Run. `HarnessState.environment_states` summarizes mount references, while the Host record remains authoritative.

A creation can allocate a target and then fail readiness. `EnvironmentManagementError` and `EnvironmentManagementCancelled` retain any observed state. `observed_environment_state(error, previous)` follows exception chains and returns that observation, falling back to the previous reference when none exists. A confirmed `None` means the reference was cleared. Publish the observation under the Host's conditional-update policy before releasing the lifecycle claim.

## Explicit destruction

When the Host decides to retire a target, use its management provider. This fragment uses the same Host publication and concurrency setup:

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

Clear the saved reference after confirmed destruction or absence. An unknown outcome retains the known reference for inspection. Destruction targets the validated resource and Provider-owned material; Host directories, bind sources, and unrelated volumes remain with their owners.

## Management and execution methods

Use this table to choose the operation for the current phase:

| Method                                                     | Result or effect                                                   |
| ---------------------------------------------------------- | ------------------------------------------------------------------ |
| `definition.open_provider(...)`                            | Account management client; does not select or mutate a target      |
| `provider.create(recipe, ..., operation_id=...)`           | New target or reconciliation of the same allocation                |
| `provider.start(recipe, ..., state=..., operation_id=...)` | Start the represented target under its resume contract             |
| `provider.inspect(recipe, ..., state=...)`                 | Read-only status (`running`, `stopped`, or `absent`) and reference |
| `provider.stop(...)` / `destroy(...)`                      | Explicit lifecycle mutation where supported                        |
| `provider.keepalive(...)`                                  | Renewal scheduled by the Host                                      |
| `definition.execution.execution_connector(...)`            | Validated fixed-target connector                                   |
| `connector.open()`                                         | New ready execution                                                |
| `execution.check_ready(families)`                          | Read-only check of the current execution                           |
| `execution.close()`                                        | Idempotent cleanup of that execution                               |

Connectors remain usable after their management client closes. Borrowed runtimes have a separate Host owner: pass account settings if needed, omit credentials when borrowing, and close the runtime after its borrowers finish. Local Envd requires this Host runtime. HTTP and WebSocket Envd are connect-only providers.

## Handle failures

Inspect the error before deciding what to do next. Management and Provider boundaries use `EnvironmentProviderError`; operations use `EnvironmentError`. Show `safe_projection()` to users and retain the full exception for local diagnostics.

| Outcome certainty | Next step                                                                 |
| ----------------- | ------------------------------------------------------------------------- |
| `not_dispatched`  | Correct input or runtime conditions, then decide whether to try again     |
| `known`           | Handle the confirmed outcome; check separately whether retry is supported |
| `unknown`         | Inspect or reconcile the original operation and target before retrying    |

Control-plane failure is unavailable evidence, not proof that the target is absent. To reconnect, explicitly open a new execution or replace the Harness mount. Use new process, output, and computer references in the new execution.

Envd can reattach a retained Session using a [Host recovery hint](remote-envd.md#state-concurrency-and-recovery). That hint is separate from portable target state. Reattachment neither replays operations nor restores old execution references. Consult the [Provider comparison](providers.md#reconnection-and-lifecycle) for stop, filesystem retention, and expiry behavior.
