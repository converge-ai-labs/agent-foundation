# Client-side tools

Use client tools when the model can request an action but a separate application must execute it: a browser action, an external application integration, or another authenticated client. The declaration supplies model guidance and JSON argument schemas, not a Python executor or credential.

A root Run suspends with correlated external calls. The Host authenticates the executor, performs or collects the action, persists the result, and resumes a fresh Run. For in-process functions, use [Tools and dependencies](tools-and-dependencies.md) instead.

## Declare, suspend, and resume offline

This complete example uses a deterministic model and a simulated external result. It makes no provider request and performs no browser action:

```python
import asyncio

from a13n_harness import DeferredToolResume, HarnessBuilder, RunBindings
from a13n_harness.tools import (
    ClientToolDefinition,
    ClientToolsCapability,
    ClientToolsetDefinition,
    ClientToolsSpec,
)
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
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
        capabilities=(
            ClientToolsCapability(
                spec=ClientToolsSpec(default_toolsets=(declaration,))
            ),
        ),
    )
    first = await executable.run("Open the guide", bindings=RunBindings.embedded())
    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None
    requests = first.deferred
    call = requests.calls[0]
    assert call.tool_name == "open_document"

    # A real Host authenticates the client and validates/persists its result here.
    results = requests.build_results(calls={call.tool_call_id: {"opened": True}})
    resumed = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, results),
    )
    assert resumed.output_or_raise() == "The client action finished."
    print(resumed.output_or_raise())


asyncio.run(main())
```

1. `ClientToolsCapability` originates from the **definition** and owns the effective surface.
2. The model requests a tool, but Harness does not execute an application action in process.
3. `first.deferred.calls` provides the exact call identity; `build_results()` preserves correlation and category validation.
4. Resume uses the selected state and **fresh bindings**, not a still-open stream or an arbitrary new user message.

[State and Resume](state-and-resume.md) explains complete feedback coverage, approval categories, state selection, and cleanup. Serializing a checkpoint alone does not persist an external command's outcome.

## Definition and Run selection

`ClientToolsSpec` has two fields:

| Field                | Default | Meaning                                                     |
| -------------------- | ------- | ----------------------------------------------------------- |
| `default_toolsets`   | `()`    | Definition-owned default declarations                       |
| `allow_run_override` | `False` | Whether the Host can replace the complete surface for a Run |

When explicitly allowed, supply `RunBindings.client_toolsets`. This is **whole-list replacement**, not a merge; `client_toolsets=()` clears the surface, while `None` retains the defaults. A binding cannot independently install the definition owner or change its override policy. The effective declarations are validated and copied for the Run.

Use stable `toolset_id` and tool names. A resumed external call must still match the selected declaration and current continuation contract; changing a schema or swapping executors is not a way to accept mismatched pending results.

## Declaration fields and bounds

`ClientToolDefinition` contains `name`, `description`, `parameters_json_schema`, optional `instruction`, and JSON `metadata`. The argument schema must declare `type: "object"`. Instructions are model guidance, not enforced approval policy. Declarations are detached from mutable caller inputs.

| Boundary                           | Limit                  |
| ---------------------------------- | ---------------------- |
| Toolsets / total tools             | 32 / 128               |
| Toolset ID / tool name             | 256 characters each    |
| Description / optional instruction | 16,384 characters each |
| Argument schema                    | 64 KiB of encoded JSON |
| Metadata                           | 16 KiB of encoded JSON |

Each toolset must contain at least one tool. Toolset IDs and effective tool names are unique; names follow the declared identifier pattern. Metadata rejects reserved Harness keys and authority-bearing keys such as credentials, grants, and policy. Do not put live clients, authentication, executable callbacks, or server authorization claims in portable declarations.

## Authority and child Runs

The Host owns approval UI, executor authentication, command idempotency, durable pending records, timeout/cancellation policy, and result acceptance. Client-provided metadata cannot authorize a server-side tool. [Managed tools](managed-tools.md) covers the independent in-process invocation boundary.

Child Runs can defer when the current Host supports feedback. A Host without that lifecycle sets `RunBindings.deferred_tools_supported=False`, which omits declaratively deferred tools, denies dynamic deferral, and fails unexpected terminal deferral with `deferred_tools_unsupported`. Do not create waiting work from an observation event; retain the exact terminal pending batch and checkpoint. [Built-in inline execution](delegation-and-codeact.md#host-managed-feedback) disables deferred tools; a supporting Host resumes its own child directly.
