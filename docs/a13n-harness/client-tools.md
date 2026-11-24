---
title: Client-side tools
description: Declare external client tools in Harness, suspend execution when called, and resume the thread after the caller's frontend or worker returns results.
---

Use client tools when an action belongs to another application—such as opening an editor document, driving a local UI, or calling an integration with client-held credentials. You declare the tool's name and JSON Schema in Harness; when the model calls it, the Run suspends so your Host can deliver the request to the client and resume with the returned result.

For in-process Python functions, use [Tools and dependencies](tools-and-dependencies.md). For built-in structured prompts, use [Human-in-the-loop tools](human-in-the-loop.md).

## Declare, suspend, and resume offline

Save the following offline example as `client_demo.py` in your [Quickstart](getting-started.md) environment:

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
    call = requests.calls[0].target_call
    assert client_tool_name(call, requests.metadata.get(call.tool_call_id, {})) == "open_document"
    assert call.args_as_dict() == {"document_id": "guide"}

    results = requests.build_results(calls={call.tool_call_id: {"opened": True}})
    resumed = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, results),
    )
    assert resumed.thread_id == first.thread_id and resumed.run_id != first.run_id
    print(resumed.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run --no-sync python client_demo.py` to print `The client action finished.`:

1. `ClientToolsCapability` registers model-facing declarations without local execution callbacks.
2. `first.deferred.calls` exposes pending client calls alongside `first.state`. Validate arguments against your schema in the Host before executing client work, and persist the checkpoint and pending batch ([State and resume](state-and-resume.md)).
3. `requests.build_results(...)` correlates results by `tool_call_id`, and `DeferredToolResume` starts the continuation Run. To report a client-side failure instead, pass `ToolFailed`:

```python
from pydantic_ai import ToolFailed

results = requests.build_results(
    calls={call.tool_call_id: ToolFailed("The client could not open the document.")}
)
```

## Definition, Run selection, and bounds

Configure static declarations at build time, or let the Host replace them per Run:

| Field / boundary                 | Value / behavior                                                                                                                    |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `default_toolsets`               | Default `()` declarations used when no Run override is supplied                                                                     |
| `allow_run_override`             | Default `False`; when `True`, `RunBindings.embedded(client_toolsets=(...,))` replaces the list (`()` clears, `None` keeps defaults) |
| `permission`                     | `inherit` (defaults to `allow`), `allow`, or `deny` (`ask`/`review` unsupported; confirm in the Host workflow)                      |
| Toolsets / total tools           | Up to `32` toolsets and `128` total tools; unique IDs and `[A-Za-z_][A-Za-z0-9_.:-]*` names (`<= 256` chars)                        |
| Description / instruction / JSON | `16,384` chars each; object `parameters_json_schema` `<= 64 KiB`; portable `metadata` `<= 16 KiB`                                   |

Supply the exact effective declarations again when resuming a pending batch. Keep credentials, live connections, confirmation, idempotency, and timeouts in the Host, starting client execution only from the terminal `suspended` result. Child Agents do not inherit parent client declarations automatically, and Hosts that cannot collect feedback set `RunBindings.deferred_tools_supported=False`.

## Proxy discovery

Group large client toolsets behind [ToolSearch](tool-search.md) while keeping delivery routing intact:

```python
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection

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

When the model invokes `call_tool` (`{"group": "workspace", "tool": "open_document", "arguments": {"document_id": "guide"}}`), the stored target call name becomes `workspace__open_document`, while `client_tool_name(call, requests.metadata.get(call.tool_call_id, {}))` still returns `"open_document"`. Keep the stored target intact and key both success values and `ToolFailed` results by `call.tool_call_id`.
