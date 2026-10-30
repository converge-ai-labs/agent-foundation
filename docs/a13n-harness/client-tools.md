---
title: Client-side tools
description: Let a client application execute a requested action, then resume the Harness Run with its result.
---

Use client tools when an action belongs to another application: opening an editor document, operating a browser, or calling an integration with the client's credentials. You declare the tool's name and arguments in Harness. The model requests it, the Run suspends, and your Host delivers the request to the client. The Host then starts a new Run with the client's result.

For Python functions executed inside the Run, use [Tools and dependencies](tools-and-dependencies.md). For the built-in question form, use [Human-in-the-loop tools](human-in-the-loop.md); `ask_user_question` already has its own declaration.

## Declare, suspend, and resume offline

Start with the Python 3.13 environment from [Getting started](getting-started.md). Save the following as `client_demo.py`. It uses a deterministic model and a simulated client result, so no credentials or browser are needed.

```python
import asyncio

from a13n_harness import DeferredToolResume, HarnessBuilder, RunBindings
from a13n_harness.tools import (
    ClientToolDefinition,
    ClientToolsCapability,
    ClientToolsetDefinition,
    ClientToolsSpec,
)
from a13n_harness.tools.client import client_tool_name
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel


async def model_stream(messages, info):
    returns = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    if returns:
        assert returns[-1].content == {"opened": True}
        yield "The client action finished."
    else:
        yield {
            0: DeltaToolCall(
                name="open_document",
                json_args='{"document_id":"guide"}',
                tool_call_id="external-1",
            )
        }


async def main():
    declaration = ClientToolsetDefinition(
        toolset_id="document-client",
        tools=(
            ClientToolDefinition(
                name="open_document",
                description="Open a document in the client application.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"document_id": {"type": "string"}},
                    "required": ["document_id"],
                    "additionalProperties": False,
                },
            ),
        ),
    )
    client_tools = ClientToolsCapability(
        spec=ClientToolsSpec(default_toolsets=(declaration,))
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
        capabilities=(client_tools,),
    )
    first = await executable.run("Open the guide", bindings=RunBindings.embedded())
    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None
    requests = first.deferred
    assert len(requests.calls) == 1 and not requests.approvals
    call = requests.calls[0].target_call
    name = client_tool_name(call, requests.metadata.get(call.tool_call_id, {}))
    assert name == "open_document"
    assert call.args_as_dict() == {"document_id": "guide"}

    # A real Host authenticates the client and validates/persists its result here.
    results = requests.build_results(calls={call.tool_call_id: {"opened": True}})
    resumed = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, results),
    )
    assert resumed.run_id != first.run_id
    assert resumed.thread_id == first.thread_id
    assert resumed.output_or_raise() == "The client action finished."
    print(resumed.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Run the file in that environment:

```bash
uv run --no-sync python client_demo.py
```

Expected output:

```text
The client action finished.
```

The assertions check both sides of the handoff: the client receives `open_document(document_id="guide")`, and the model receives `{"opened": True}`. The resumed Run has a new ID while retaining the same Thread.

Read the example in three parts:

1. `ClientToolsCapability` installs the declaration when you build the Agent. The declaration contains model guidance, not an execution callback.
2. The first result supplies the checkpoint and complete pending batch. Client calls appear in `requests.calls`; approvals have a separate category.
3. `build_results()` correlates feedback with the target call ID. `DeferredToolResume` carries that feedback into a new Run with the previous state and fresh bindings.

For a real integration, replace the simulated result with client delivery and result collection. Validate arguments against the accepted schema before executing the action: the external tool schema guides the model, but Harness does not run a local function validator for the client. Persist the pending batch, checkpoint, effective declarations, and accepted result according to your application's recovery needs. [State and Resume](state-and-resume.md) explains complete batch feedback and persistence ordering.

To report a failed client action, construct a typed failure in place of the successful `results` line:

```python
from pydantic_ai import ToolFailed

results = requests.build_results(
    calls={call.tool_call_id: ToolFailed("The client could not open the document.")}
)
```

Pass this result through the same `DeferredToolResume` path. Adapt the demo model's success assertion before testing this variant. A failure is still feedback for the external call, not an approval decision.

## Definition and Run selection

Keep a fixed client toolset in `default_toolsets` when every Run uses the same application interface. Allow overrides when the Host selects the available client interface for each Run:

| Field                | Default | Use                                                    |
| -------------------- | ------- | ------------------------------------------------------ |
| `default_toolsets`   | `()`    | Declarations used when no Run override is supplied     |
| `allow_run_override` | `False` | Allow the Host to replace those declarations for a Run |

With `allow_run_override=True`, pass the replacement tuple as `RunBindings.embedded(client_toolsets=(declaration,))`. The tuple replaces the whole list. `client_toolsets=()` clears the list; `None` keeps the defaults. The binding needs the definition-owned `ClientToolsCapability`, and cannot change its override policy. Harness validates and copies the selected declarations for the Run.

Retain the exact effective declarations for a pending batch and supply them again on resume. Harness checks call IDs, categories, and the current tool names and kinds. The Host compares the saved declarations or their digest: message history alone cannot establish that a same-named tool still has the same schema or executor.

## Declaration fields and bounds

A `ClientToolDefinition` contains the required `name`, `description`, and `parameters_json_schema`, plus optional `instruction`, JSON `metadata`, and `permission`. The schema must have `type: "object"`. Use the description and instruction to explain when and how the model should request the action.

`permission` accepts `inherit` (the default), `allow`, or `deny`. For client tools, `inherit` supplies an `allow` default. The Agent's ToolPermissions configuration can override that default. Effective `ask` and `review` modes are unsupported for external tools; implement client-side confirmation in the Host's delivery workflow.

| Boundary                           | Limit                  |
| ---------------------------------- | ---------------------- |
| Toolsets / total tools             | 32 / 128               |
| Toolset ID / tool name             | 256 characters each    |
| Description / optional instruction | 16,384 characters each |
| Argument schema                    | 64 KiB of encoded JSON |
| Metadata                           | 16 KiB of encoded JSON |

Each toolset needs at least one tool. Toolset IDs and tool names must be unique across the effective client list. A tool name matches `[A-Za-z_][A-Za-z0-9_.:-]*`. Declarations copy their JSON data so later caller mutations do not change the selected values.

Keep credentials, live connections, callbacks, and authorization in the Host. Metadata is portable model-facing data; validation rejects reserved Harness keys and authority-bearing keys, including nested credentials, grants, and policy claims. Instructions explain behavior but do not enforce approval.

## Authority and child Runs

The Host authenticates the executor, validates the action and result, and owns confirmation, idempotency, durable pending records, timeouts, and cancellation. Start client work from the terminal suspended result and the Host's accepted record. Tool-call observation events alone do not establish a waiting command. In-process authorization is a separate boundary described in [Managed tools](managed-tools.md).

A child can use client tools when its own definition enables them and its Host supports the feedback lifecycle. The parent's client declarations are not automatically inherited. The Host supplies fresh child bindings and resumes that child with its own pending batch.

A Host that cannot collect feedback sets `RunBindings.deferred_tools_supported=False`. Harness removes declaratively deferred tools, denies dynamic deferral, and reports `deferred_tools_unsupported` for an unexpected terminal deferral. [Built-in inline execution](delegation-and-codeact.md#host-managed-feedback) uses this mode.

## Proxy discovery

For a large client interface, group its tools behind [ToolSearch](tool-search.md). Continue with `client_demo.py`: add the following import and replace the builder block in `main()` with this configuration. Keep the existing `client_tools` instance.

```python
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection

# Inside main(), after creating client_tools:
search = ToolSearchPlan(
    groups={
        "workspace": ToolSearchSelection(
            "Document actions", capabilities=(client_tools,)
        )
    }
)
executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=model_stream),
    capabilities=(client_tools,),
    tool_search=search,
)
```

In `model_stream()`, replace the `DeltaToolCall` entry with this one. The deterministic model already knows the schema; a general model first discovers or describes the tool.

```python
0: DeltaToolCall(
    name="call_tool",
    json_args=(
        '{"group":"workspace","tool":"open_document",'
        '"arguments":{"document_id":"guide"}}'
    ),
    tool_call_id="external-1",
)
```

Run `uv run --no-sync python client_demo.py` again. The expected output is unchanged. The stored target is now `workspace__open_document`, while `client_tool_name()` in the existing delivery code still returns `open_document`. Keep the stored target intact and key results by its `tool_call_id`.

`toolset_id` identifies the client source; the ToolSearch group controls discovery. Pending metadata retains the routing marker's `declared_name` and `toolset_id`, not arbitrary declaration metadata. Persist the complete pending batch with the checkpoint. Success values and native `ToolFailed` use the same target ID. Native inline deferred handlers can also use `client_tool_name()` with their call and per-call metadata. Client declarations support ToolSearch grouping, but not arbitrary renaming wrappers.
