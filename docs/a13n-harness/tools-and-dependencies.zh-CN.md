---
title: 工具与依赖
description: 为模型提供有类型的 Python 工具，并为每次 Run 提供所需的依赖。
---

工具让模型能够请求应用执行操作。使用普通的类型化 Python 函数，通过 Pydantic AI Capabilities 或 Toolset 分组，并只向每次 Run 提供它应该使用的依赖。

Harness 不替换 Pydantic AI 的工具 schema 或调度器。它增加必需的结果边界，并为明确纳入管理的工具提供调用策略和资源处理。

## 离线运行函数工具

这个完整示例无需网络，通过真实 Agent 执行循环调用 `double(4)`。在[源码快速入门](getting-started.md)的工作空间中保存为 `tools_example.py`，运行 `uv run python tools_example.py`。

```python
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

1. 类型注解定义参数 schema，docstring 向模型说明操作。
2. `Capability(tools=[double])` 将函数纳入原生组合。
3. 确定性模型发出工具调用，收到结果，再返回文字。
4. `output_or_raise()` 返回最终校验后的回答，不是某个工具的返回值。

工具较多时，可使用 `Capability(toolsets=[FunctionToolset([...], id="...")])`，其中 `FunctionToolset` 来自 `pydantic_ai.toolsets`。工具仍属于各自 Capability，没有另一套并行的 Harness 工具注册 API。

## 访问当前 Run

需要上下文的函数，以 `RunContext[AgentContext]` 作为首个参数。Pydantic AI 会注入它，模型不能自行提供。

```python
from a13n_harness import AgentContext, RunBindings
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability


def request_label(ctx: RunContext[AgentContext]) -> str:
    """Return the application's label for this request."""
    label = ctx.deps.metadata.get("request_label", "unlabeled")
    if not isinstance(label, str):
        raise TypeError("request_label must be a string")
    return label


capability = Capability(id="request-context", tools=[request_label])
bindings = RunBindings.embedded(metadata={"request_label": "support-triage"})
```

构建时传入 `capability`，调用 `run()` 或 `stream()` 时传入新的 `bindings`。`ctx` 包含原生消息、用量和限制；`ctx.deps` 包含 Harness 身份、Thread/Run 关联、Environment、插件、状态和元数据。

元数据是大小受限的应用上下文，**不是授权依据**。不要放入密钥、token 或活跃数据库 session。可信身份 claim 也必须结合当前策略，才能授权某个操作。

## 提供活跃的应用依赖

Harness 将原生依赖类型固定为 `AgentContext`，不接受第二个应用 `deps_type`，也不接受 `run()` 上的通用应用服务参数。内置功能请使用已有类型化 `RunBindings` 字段，例如 `web` 或 `document_converter`。

对于应用工具，如果应用会为每次请求构建 executable，那么捕获已授权服务的闭包就足够。下面假定应用管理 `inventory` 及其生命周期，并提供原生 `model`：

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

这个 Capability 属于构建时组合，不放在 `RunBindings.capabilities` 中。后者只接受文档列出的调用策略和上游 MCP 类型，不是任意应用服务或工具的注入入口。

如果应用跨请求复用同一个 executable，应在构建时安装自定义 `AbstractCapability[AgentContext]`，在原生 `for_run(ctx)` 中，根据可信的当前 Run 身份和应用管理的 factory 解析服务。每次内部模型恢复尝试都会重新执行原生 Run 绑定。也可以通过 [Harness 插件](plugins.md#plugin-lifecycle)，每个逻辑 Run 只绑定一次新实例；插件贡献的工具或 Capabilities 用 `ctx.deps.plugins.require(plugin_id, ExpectedPluginType)` 获取实例。两种方式都不需要第二个依赖容器；并发 Run 不得通过修改共享原型来选择服务。

服务必须自行约束作用范围，不能让模型选择租户或凭据。数据库事务应围绕单次操作，不要覆盖整个模型和工具循环。应用或所属扩展必须明确管理资源获取和清理，`for_run()` 本身不会关闭客户端。绝不要把活跃服务放进 `metadata`、序列化进 `HarnessState`，或把已经认证的 Run 绑定实例当作续接状态复用。

## 原生工具、托管工具与 Provider 原生工具

| 工具路径              | 示例                           | 边界                                                   |
| --------------------- | ------------------------------ | ------------------------------------------------------ |
| 可信 Python 函数      | `double`、应用服务             | 原生工具调度与 Harness 结果处理，Python 代码负责副作用 |
| 托管 Environment 工具 | 文件编辑或 shell 执行          | 类型化资源、当前策略、Provider 强制约束和有界结果      |
| 本地 MCP 工具         | 来自已连接 MCP server 的工具   | 原生 MCP 传输与本地函数工具结果边界                    |
| Provider 原生工具     | 模型 provider 的搜索或图像工具 | 由 provider 执行，不是本地 Python 工具调用             |
| 延后工具              | 人工审批或外部、客户端执行     | 根 Run 暂停，Host 在新 Run 中提供关联输入              |

名字叫 `safe_shell` 不意味着工具已纳入管理；托管元数据才会选择额外的策略路径。Python 回调仍可使用其进程的环境权限，工具可见性不等于 OS 隔离。

完整 `HarnessTool` 示例和调用策略参考，参阅[托管工具与策略](managed-tools.md)。浏览器或其他外部客户端执行声明的工具时，参阅[客户端工具](client-tools.md)。[环境工具](environments.md)、[MCP 工具](mcp.md)和[延后恢复](state-and-resume.md)说明了对应的集成与续接方式。

## 错误、重试与输出限制

- 原生工具参数校验和模型可见的重试行为由 Pydantic AI 管理，通过 `AgentSpec.retries` 配置工具和输出重试。
- 传输重试由 provider 客户端管理。模型重试不能作为重复结果不确定的副作用操作的许可。
- 托管调用策略可在发出调用前拒绝或要求审批。可选 shell 审查只能增加限制。
- 工具结果在进入模型历史前受到大小限制。过大的本地 MCP 文本或 JSON 默认明确截断；这不是传入 HTTP 请求体限制，也不是持久附件存储。

既要测试模型可见的 schema，也要测试实际依赖调用。会修改状态的工具，还应在应用边界测试拒绝、取消和结果不确定的情况。

## 下一步

- [Capabilities](capabilities.md)：组合功能行为和自定义声明式类型。
- [输入与输出](inputs-and-outputs.md)：区分工具结果与 Agent 最终输出。
- [测试](testing.md)：无需在线 provider，验证工具和续接。
