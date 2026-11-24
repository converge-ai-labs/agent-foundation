---
title: 能力（Capabilities）
description: 在 Harness 中组合模块化智能体能力，包括文件工具、联网搜索、Shell 审查、工作状态、循环检测以及自定义能力。
---

**Capability** 为智能体添加模块化能力：工具、指令、上下文、记忆或生命周期钩子。在构建可执行体时组合能力，并在发起 Run 时通过 `RunBindings` 提供请求级客户端。

## 添加自定义能力

保存为 `capability_demo.py` 并运行 `uv run python capability_demo.py`：

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

## 组合来源

| 来源                  | 挂载方式                                          |
| --------------------- | ------------------------------------------------- |
| Python 应用代码       | `HarnessBuilder.build(..., capabilities=...)`     |
| 可移植 `AgentSpec`    | `AgentSpec.capabilities`（内置可序列化能力定义）  |
| 已启用的 Harness 插件 | 在 `plugin.get_capabilities()` 中返回实例         |
| 单次运行策略 / MCP    | `RunBindings.capabilities`（调用策略与 MCP 组合） |

功能客户端（如 `web`、`document_converter`、`skill_selection`、`task_state`）应通过 `RunBindings` 的类型化字段传入，而非 `RunBindings.capabilities`。

## 内置能力一览

| 类别           | 能力                                                                            | 指南                                        |
| -------------- | ------------------------------------------------------------------------------- | ------------------------------------------- |
| 环境与文件     | `DynamicEnvironmentCapability`、`FileContextCapability`                         | [运行环境](environments.md)                 |
| 上下文与状态   | `RuntimeContextCapability`、`WorkingStateCapability`、`CompactionCapability`    | [上下文与工作状态](context.md)              |
| 委派与 CodeAct | `SubagentCapability`、`CodeActCapability`                                       | [委派与 CodeAct](delegation-and-codeact.md) |
| 技能与记忆     | `SkillsCapability`、`FileMemoryCapability`、`RecordMemoryCapability`            | [技能](skills.md)、[记忆](memory.md)        |
| Web 与 MCP     | `WebCapability`、`ToolSearchCapability`                                         | [MCP](mcp.md)、[工具搜索](tool-search.md)   |
| 安全与人机协作 | `LoopDetectionCapability`、`ShellReviewCapability`、`UserInteractionCapability` | [人机协作](human-in-the-loop.md)            |

## 检测重复动作循环

添加 `LoopDetectionCapability` 检测重复工具调用、相同结果、交替调用或重复文本：

- `action="nudge"`（默认）：提示模型更换思路。
- `action="error"`：检测到循环时立即终止本次运行。
- 如存在合法轮询场景，可调高阈值或将 `repeated_call_threshold=None` / `repeated_result_threshold=None`。

## 通过对象存储复用原始媒体

挂载对象存储后，大型工具输出与二进制媒体只捕获一次，后续通过轻量引用传递，避免在对话历史中反复重复 base64 负载。

### 对象引用传输

通过 `object_ref_tools` 匹配规则（精确工具名或 `*` 通配符）为指定工具启用对象引用传输。Harness 会在权限和审批检查通过后、实际执行时将引用解析为 base64 字符串，保持历史记录与审批卡片简洁。

宿主侧配置见 [Harness UI](../a13n-harness-ui/configuration.md#object-store) 与 [Service](../a13n-service/agent-configuration.md#reuse-media-between-tools)。
