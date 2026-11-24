---
title: 工具与依赖
description: 在 Capability 中注册带类型注解的 Python 函数工具，读取 RunContext[AgentContext]，并安全绑定单次运行的应用服务依赖。
---

将带类型注解的 Python 函数或异步协程注册进 `Capability`，再传入 `HarnessBuilder().build(...)` 即可向模型暴露工具。Pydantic AI 负责生成 JSON Schema 并校验参数；Harness 负责结果长度边界与执行生命周期。

## 离线运行函数工具

将以下自包含示例保存为 `tools_example.py` 并运行 `uv run python tools_example.py`：

```python title="tools_example.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)


def double(value: int) -> int:
    """Return twice the supplied integer."""
    return value * 2


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | DeltaToolCalls]:
    del info
    results = [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "double"
    ]
    if results:
        yield f"Result: {results[-1]}"
    else:
        yield {
            0: DeltaToolCall(
                name="double", json_args='{"value": 4}', tool_call_id="call-double"
            )
        }


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(Capability(id="math", tools=[double]),),
    )
    result = await executable.run("Double four.")
    print(result.output_or_raise())  # Result: 8


if __name__ == "__main__":
    asyncio.run(main())
```

类型注解定义模型可见的参数结构，文档字符串说明调用时机。工具数量较多时，可使用 `pydantic_ai.toolsets` 中的 `FunctionToolset([...], id="...")` 装入 `Capability(toolsets=[...])`。

## 访问当前运行上下文

当工具需要读取运行标识、执行环境、插件、状态或有界 JSON 元数据时，将首个参数声明为 `ctx: RunContext[AgentContext]`。Harness 会在调用时自动注入 `ctx`，且不会将其暴露到模型可见的 Schema 中：

```python
from a13n_harness import AgentContext, RunBindings
from pydantic_ai import RunContext


def request_label(ctx: RunContext[AgentContext]) -> str:
    """Return the application's label for this request."""
    label = ctx.deps.metadata.get("request_label", "unlabeled")
    if not isinstance(label, str):
        raise TypeError("request_label must be a string")
    return label


# 通过 RunBindings 传入单次运行的 JSON 元数据：
# bindings = RunBindings.embedded(metadata={"request_label": "support-triage"})
```

`ctx` 暴露消息、用量与限额；`ctx.deps`（`AgentContext`）暴露 `run_id`、`thread_id`、`instance`、`environment`、`plugins`、`state` 与 `metadata`。请勿将凭据或活跃客户端塞入 `metadata` 或 `HarnessState`。

## 注入活跃应用依赖

Harness 将 Pydantic AI 的依赖类型固定为 `AgentContext`，不支持自定义第二套 `deps_type`。请根据可执行体的复用周期选择注入模式：

| 应用模式                   | 依赖注入方式                                                                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| 每次请求构建一个可执行体   | 在工具函数中直接闭包捕获已鉴权的服务实例                                                                                                         |
| 跨多个请求复用同一可执行体 | 通过 `AbstractCapability[AgentContext].for_run(ctx)` 或 [Harness 插件](plugins.md#plugin-lifecycle)（`ctx.deps.plugins.require(...)`）按运行解析 |
| 使用内置集成               | 通过 `RunBindings` 对应的类型化字段传入（如 `environment`、`web`、`document_converter`）                                                         |

单次请求构建可执行体时，直接闭包捕获已鉴权服务，并将数据库事务收敛在单次查询内，切勿跨整个智能体循环持有事务：

```python
from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.capabilities import Capability


async def inspect_inventory(inventory, model):
    async def stock_count(sku: str) -> int:
        """Look up stock for one product in the current authorized inventory."""
        return await inventory.stock_count(sku)

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model,
        capabilities=(Capability(id="inventory", tools=[stock_count]),),
    )
    return await executable.run("Check stock for SKU-123")
```

## 工具执行路径、重试与输出边界

| 目标动作                         | 对应集成                                                        |
| -------------------------------- | --------------------------------------------------------------- |
| 调用受信任的 Python 应用代码     | `Capability(tools=[...])` 中的 Python 函数                      |
| 在沙箱策略下编辑文件或运行命令   | [受管工具](managed-tools.md)与[执行环境](environments.md)       |
| 连接外部工具服务器               | [MCP](mcp.md)                                                   |
| 挂起等待人工审批或前端执行       | [人机协同](human-in-the-loop.md)或[客户端工具](client-tools.md) |
| 让智能体用 Python 编排多工具调用 | [子智能体委派与 CodeAct](delegation-and-codeact.md)             |

参数校验失败会将结构化错误反馈给模型自纠（上限由 `AgentSpec.retries` 控制；包含外部副作用的操作在重试前应先确认状态）。超大工具返回结果会在写入模型消息历史前自动截断落盘。
