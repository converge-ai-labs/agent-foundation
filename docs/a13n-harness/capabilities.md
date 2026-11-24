---
title: Capabilities
description: Compose modular agent features—including file tools, web search, shell review, working state, loop detection, and custom Capabilities—in Harness.
---

A **Capability** adds a feature to an Agent: tools, instructions, context, memory, or lifecycle hooks. Compose Capabilities when building the executable, and supply request-scoped clients through `RunBindings`.

## Add a custom Capability

Save as `capability_demo.py` and run `uv run python capability_demo.py`:

```python title="capability_demo.py"
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from a13n_harness import AgentContext, AgentSpec, HarnessBuilder, RunBindings
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


@dataclass(kw_only=True)
class PolicyInstructionsCapability(AbstractCapability[AgentContext]):
    instructions: str
    id: str | None = "policy-instructions"

    @classmethod
    def get_serialization_name(cls) -> str:
        return "policy_instructions"

    def get_instructions(self) -> str:
        return self.instructions


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del messages, info
    yield "Capability instructions received."


executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    capabilities=(PolicyInstructionsCapability(instructions="Explain assumptions."),),
)


async def main() -> None:
    result = await executable.run("Check the setup.", bindings=RunBindings.embedded())
    print(result.output_or_raise())


asyncio.run(main())
```

## Composition sources

| Source                 | How to attach                                                      |
| ---------------------- | ------------------------------------------------------------------ |
| Python application     | `HarnessBuilder.build(..., capabilities=...)`                      |
| Portable `AgentSpec`   | `AgentSpec.capabilities` (serialized built-in Capability specs)    |
| Enabled Harness plugin | Return instances from `plugin.get_capabilities()`                  |
| Per-Run policy / MCP   | `RunBindings.capabilities` (invocation policy and MCP composition) |

Pass feature clients (such as `web`, `document_converter`, `skill_selection`, `task_state`) on typed `RunBindings` fields rather than `RunBindings.capabilities`.

## Built-in Capabilities

| Category                | Capabilities                                                                    | Guide                                               |
| ----------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------- |
| Environments & files    | `DynamicEnvironmentCapability`, `FileContextCapability`                         | [Environments](environments.md)                     |
| Context & working state | `RuntimeContextCapability`, `WorkingStateCapability`, `CompactionCapability`    | [Context and working state](context.md)             |
| Delegation & CodeAct    | `SubagentCapability`, `CodeActCapability`                                       | [Delegation and CodeAct](delegation-and-codeact.md) |
| Skills & memory         | `SkillsCapability`, `FileMemoryCapability`, `RecordMemoryCapability`            | [Skills](skills.md), [Memory](memory.md)            |
| Web & MCP               | `WebCapability`, `ToolSearchCapability`                                         | [MCP](mcp.md), [Tool search](tool-search.md)        |
| Safety & control        | `LoopDetectionCapability`, `ShellReviewCapability`, `UserInteractionCapability` | [Human-in-the-loop](human-in-the-loop.md)           |

## Detect repeated action loops

Add `LoopDetectionCapability` to detect repeated tool calls, identical results, alternating patterns, or repeated text:

- `action="nudge"` (default): asks the Model to change approach.
- `action="error"`: fails the Run immediately when a loop is detected.
- For legitimate polling loops, raise thresholds or set `repeated_call_threshold=None` / `repeated_result_threshold=None`.

## Reuse original media with Object Store

Attach an Object Store to capture large tool outputs and binary media once, then pass compact references instead of repeating base64 payloads in conversation history.

### Object Reference Transport

Opt specific tools into Object Reference Transport via `object_ref_tools` matching rules (exact tool names or `*` wildcards). Harness resolves object references back into base64 strings at execution time after permission and approval checks—keeping history and approval cards lightweight.

For Host configuration, see [Harness UI](../a13n-harness-ui/configuration.md#object-store) and [Service](../a13n-service/agent-configuration.md#reuse-media-between-tools).
