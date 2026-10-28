---
title: 工具与依赖
description: 注册 Python 函数，离线调用它，并提供当前 Run 上下文或应用服务。
---

工具让模型能够请求调用 Python 函数。先编写有类型的函数，将它放入 Capability，再把 Capability 传给 builder。然后验证完整调用：模型发出请求、Python 执行、工具返回结果、模型给出最终回答。

使用[快速入门](getting-started.md)中已同步依赖的 checkout。下方两个可执行示例均可离线运行。

## 离线运行函数工具

将以下完整程序保存为 `tools_example.py`：

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
    assert result.output_or_raise() == "Result: 8"
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

运行并检查输出是否为 `Result: 8`：

```bash
uv run python tools_example.py
```

`int` 注解定义模型可见的参数类型，docstring 说明函数用途。`Capability(id="math", tools=[double])` 在构建时注册函数。确定性 responder 请求调用 `double(4)`，随后读取 `ToolReturnPart` 并生成最终文本。`output_or_raise()` 返回最终回答，而不是工具返回的整数。

工具组较大时，从 `pydantic_ai.toolsets` 导入 `FunctionToolset`，放入 `Capability(toolsets=[FunctionToolset([...], id="...")])`。Capabilities 仍是组合工具的顶层入口。Pydantic AI 负责函数 schema 和调度，Harness 负责结果限制及工具生命周期处理。

## 访问当前 Run

工具需要应用提供的上下文时，将 `RunContext[AgentContext]` 作为首个参数。运行时注入这个参数；模型只提供其余参数。

在 `tools_example.py` 中验证此功能：保留原导入和最后的 `asyncio.run(main())`，添加下方两个导入，并替换 `double()`、`respond()` 和 `main()`：

```python
from a13n_harness import AgentContext, RunBindings
from pydantic_ai import RunContext


def request_label(ctx: RunContext[AgentContext]) -> str:
    """Return the application's label for this request."""
    label = ctx.deps.metadata.get("request_label", "unlabeled")
    if not isinstance(label, str):
        raise TypeError("request_label must be a string")
    return label


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | DeltaToolCalls]:
    del info
    for message in reversed(messages):
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == "request_label":
                yield str(part.content)
                return
    yield {
        0: DeltaToolCall(
            name="request_label", json_args="{}", tool_call_id="call-label"
        )
    }


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(Capability(id="request-context", tools=[request_label]),),
    )
    result = await executable.run(
        "Read the request label.",
        bindings=RunBindings.embedded(metadata={"request_label": "support-triage"}),
    )
    assert result.output_or_raise() == "support-triage"
    print(result.output_or_raise())
```

再次运行同一文件。输出 `support-triage`，确认工具读取了新的绑定。模型发送的是 `{}`，请求标签来自应用。

`ctx` 提供原生消息、用量和限制；`ctx.deps` 提供 Agent 身份、Thread/Run 关联、Environment、插件、状态和元数据。元数据适合放置标签等大小受限的 JSON 上下文。凭据和活跃客户端放入对应的类型化集成或应用管理的服务；授权操作依据当前策略，而不是元数据标签。

## 提供活跃的应用依赖

接入应用服务前，先明确谁管理客户端，以及授权保持有效的时间：

| 应用方式                 | 集成方法                                                                |
| ------------------------ | ----------------------------------------------------------------------- |
| 每个请求构建一个执行对象 | 在函数工具中通过闭包捕获已授权的服务                                    |
| 跨请求复用一个执行对象   | 通过自定义 Capability 或 Harness 插件解析新服务                         |
| 使用内置集成             | 提供对应的类型化 `RunBindings` 字段，例如 `web` 或 `document_converter` |

以下集成片段假定应用已获取 `inventory`，并提供原生 `model`。服务作用域结束时，也由应用关闭服务：

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

闭包确定服务，模型在这个服务内选择 SKU。服务自身约束租户和凭据范围。数据库事务应围绕单次操作，而不是整个 Agent 循环。

复用执行对象时，选择以下一种生命周期：

- **原生 Capability：** 在构建时安装 `AbstractCapability[AgentContext]`。其 `for_run(ctx)` 根据当前身份和应用管理的 factory 解析已授权服务。每次内部模型恢复尝试都会重新绑定。
- **Harness 插件：** 每个逻辑 Run 绑定一次新插件。插件的工具或所贡献的 Capabilities 通过 `ctx.deps.plugins.require(plugin_id, ExpectedPluginType)` 获取它。按[插件生命周期](plugins.md#plugin-lifecycle)完成清理。

保持共享原型可重入，不通过修改原型来选择请求专属服务。应用或扩展负责获取和清理资源；`for_run()` 本身不会关闭客户端。

Harness 的依赖类型是 `AgentContext`。普通应用服务不放入第二个 `deps_type`、`metadata` 或 `HarnessState`。普通工具 Capability 同样传给 builder；`RunBindings.capabilities` 只接受受支持的上游 MCP 和调用策略类型。

## 工具执行路径

根据操作的执行方选择集成方式：

| 操作                                  | 使用方式                                                        | 执行方                                |
| ------------------------------------- | --------------------------------------------------------------- | ------------------------------------- |
| 调用可信应用代码                      | 上文的 Python 函数                                              | Python 进程执行，Harness 处理工具结果 |
| 按 Environment 策略编辑文件或运行命令 | [托管工具](managed-tools.md)与[环境](environments.md)           | 当前策略与 Environment Provider       |
| 调用已连接的 MCP server               | [MCP](mcp.md)                                                   | 原生 MCP 传输与本地结果处理           |
| 使用 provider 搜索或图像工具          | Provider 原生工具                                               | 模型 provider                         |
| 请求审批或外部执行                    | [人机协作](human-in-the-loop.md)或[客户端工具](client-tools.md) | Host 用关联输入恢复已暂停的根 Run     |

托管工具元数据决定资源处理和调用策略路径。函数名不会启用该路径，普通 Python 回调保留进程权限。工具可见性与 OS 隔离是两回事。

## 错误、重试与输出限制

在负责该行为的层级排查问题：

| 失败或限制               | 配置或检查位置                                      |
| ------------------------ | --------------------------------------------------- |
| 工具参数或输出无效       | 原生校验与 `AgentSpec.retries`                      |
| Provider 传输失败        | Provider 客户端重试策略                             |
| 托管调用被拒绝或要求审批 | 调用策略；可选 shell 审查可增加限制                 |
| 工具结果过大             | Harness 结果限制；本地 MCP 文本或 JSON 默认明确截断 |

模型重试不能证明外部修改是否已经发生。重复副作用操作前，先检查不确定结果。工具结果限制作用于进入模型历史之前，不是 HTTP 请求体限制，也不是持久附件存储。

像离线示例一样，同时测试可见 schema 和实际调用。对会修改应用状态的工具，再添加拒绝、取消和结果不确定的测试。

## 下一步

- [Capabilities](capabilities.md)：封装可复用的工具组和上下文。
- [输入与输出](inputs-and-outputs.md)：在工具结果之外返回指定类型的 Agent 输出。
- [测试](testing.md)：将离线流程转为回归测试。
