---
title: MCP 工具
description: 调用 MCP 工具，明确客户端的生命周期，再从当前身份和 Run 生成请求头。
---

通过 MCP Capability，将 Agent 接到其他进程或服务提供的工具。先运行下面的进程内计算器，无需远程服务或模型账号，即可理解连接生命周期。再根据应用需求选择连接方式：

| 应用需求                                       | 连接方式                                         |
| ---------------------------------------------- | ------------------------------------------------ |
| 固定 URL、stdio 进程、进程内服务或现有 Toolset | 原生 `MCP`                                       |
| 需要从当前身份或 Run 选择请求头的 HTTP(S) 服务 | `ContextualMCP`                                  |
| 在交互工作台中配置服务                         | [Harness UI MCP 资源](../a13n-harness-ui/mcp.md) |

## 离线运行 MCP 工具

使用[快速开始](getting-started.md#requirements)中的 Python 3.13 源码环境，其中已包含 FastMCP 依赖。将以下完整程序保存为 `mcp_demo.py`：

```python
import asyncio
from collections.abc import AsyncIterator

from fastmcp import Client, FastMCP
from pydantic_ai.capabilities import MCP
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from a13n_harness import AgentSpec, HarnessBuilder, RunBindings

server = FastMCP("calculator")
calls = []


@server.tool
def add(a: int, b: int) -> int:
    calls.append((a, b))
    return a + b


async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
    if isinstance(messages[-1], ModelRequest):
        for part in messages[-1].parts:
            if isinstance(part, ToolReturnPart):
                yield f"Result: {part.content}"
                return
    yield {0: DeltaToolCall(name=info.function_tools[0].name, json_args='{"a": 3, "b": 5}')}


async def main() -> None:
    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond))
    async with Client(server, mode="auto") as client:
        for prompt in ("Add 3 and 5", "Add them again"):
            projection = MCPToolset(client, id="calculator", cache_tools=False)
            bindings = RunBindings.embedded(capabilities=(MCP(id="calculator", local=projection),))
            result = await executable.run(prompt, bindings=bindings)
            assert result.output_or_raise() == "Result: 8"
            print(result.output_or_raise())

    assert calls == [(3, 5), (3, 5)]


if __name__ == "__main__":
    asyncio.run(main())
```

在仓库根目录运行：

```bash
uv run --no-sync python mcp_demo.py
```

预期输出：

```text
Result: 8
Result: 8
```

`calls` 断言确认服务端函数确实执行了两次。确定性模型选择已发现的工具并读取其返回值。

示例中有三个不同的生命周期：

1. Host 只进入一次 `Client(server)`，在退出 `async with` 时关闭客户端。
2. 每次 Run 通过 `RunBindings` 接收新的 `MCPToolset` 投影。
3. 每次 `executable.run()` 管理该 Run 的工具使用和清理。

这里是共用一个已进入客户端的独立 Run。如果还需要继续同一段对话，按[状态与恢复](state-and-resume.md)传入先前结果的状态。服务连接与 Harness Thread 是不同资源。

## 原生 MCP

连接固定服务时，在 `AgentSpec.capabilities` 或 builder 的 `capabilities=` 参数中安装 `MCP`。以下构造片段声明一个 URL 服务，在本地执行其工具，并将发现范围限定为 `search`：

```python
from a13n_harness import AgentSpec

agent_spec = AgentSpec.from_dict(
    {
        "capabilities": [
            {
                "MCP": {
                    "url": "https://mcp.example.com/mcp",
                    "id": "knowledge",
                    "local": True,
                    "native": False,
                    "allowed_tools": ["search"],
                }
            }
        ]
    }
)
```

将示例 URL 替换为可访问、提供 `search` 工具的 MCP 服务。用配置好的模型构建这个 `agent_spec`，再像第一个示例那样调用执行对象。运行前配置服务需要的认证；URL 声明本身不提供凭据。

基础安装包含本地 URL 和 stdio MCP 支持。进程内服务、传输对象、脚本路径或现有 `MCPToolset` 使用 Python 构造。需要按需加载 Capability 时，设置 `defer_loading=True` 并使用 `load_capability`。需要兼容的 provider 自行执行 URL 服务时，选择 `native=True, local=False`；下文会对比这些执行方式。

## Host 持有的客户端

多个 Run 需要复用服务连接或协议状态时，保留一个已进入的 FastMCP 客户端。第一个程序已采用此模式。连接远程服务时，将 `Client(server, mode="auto")` 替换为配置了实际 URL 和认证的客户端，同时继续在 Run 循环中创建新投影。

进入客户端前，设置认证和输入处理器。共享客户端只对应一套身份和请求头配置。FastMCP 管理多轮输入和请求状态。`mode="auto"` 选择现代发现或旧版协商，也可以显式选择 `legacy` 或 `2026-07-28`。

保持客户端开启，直到使用它的 Run 结束，再退出其上下文。检查点保存对话状态，不保存这条连接。修改操作丢失响应时，应先检查服务端结果再重复调用。

## 用 `ContextualMCP` 生成当前 Run 的请求头

服务的请求头需要当前用户、Thread 或 Run 值时，使用 `ContextualMCP`。Harness 在准备 MCP 连接之前，为每个逻辑 Run 解析一次选定值。新 Run 会取得新快照，当前 Run 内部的模型恢复尝试复用已有快照。

以下集成函数接收配置好的模型、可访问的 MCP URL，以及经 Host 认证且包含 `user_id` 的身份。它使用本地 MCP 执行，并添加固定应用请求头、当前标识和示例请求元数据。将函数保存到应用模块，在应用的异步入口中调用 `await query_knowledge(model, url, identity)`。使用前，在 Capability 中补充服务要求的其他认证配置。

```python
from a13n_harness import (
    AgentIdentityRef,
    AgentSpec,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)

from pydantic_ai.models import Model


async def query_knowledge(model: Model, url: str, identity: AgentIdentityRef) -> str:
    mcp = ContextualMCP(
        url,
        id="knowledge",
        native=False,
        local=True,
        headers={"X-Application": "support"},
        headers_factory=MCPContextHeaders(
            MCPContextHeadersConfig(
                headers={
                    "X-Run-ID": MCPContextHeaderBinding("context.run_id"),
                    "X-Thread-ID": MCPContextHeaderBinding("context.thread_id"),
                    "X-User-ID": MCPContextHeaderBinding("identity.user_id"),
                    "X-Request-Context": MCPContextHeaderBinding(
                        "context.metadata.request_context",
                        required=False,
                    ),
                }
            )
        ),
    )

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model,
        capabilities=(mcp,),
    )

    bindings = RunBindings.embedded(
        identity=identity,
        metadata={
            "request_context": {
                "region": "us-east",
                "labels": ["interactive", "priority"],
            }
        },
    )
    result = await executable.run("Find the account record", bindings=bindings)
    return result.output_or_raise()
```

服务收到 `X-Application: support`、当前 Run 和 Thread ID、传入的 `user_id`，以及紧凑 JSON 格式的 `X-Request-Context`。将示例元数据改为应用明确需要分享的值。`context.metadata.<key>` 选择一个确切的顶层键，不会遍历任意上下文属性。

声明式绑定支持以下选择器：

| 来源                                | 解析值                                       |
| ----------------------------------- | -------------------------------------------- |
| `identity.issuer`                   | 工作负载身份的签发方                         |
| `identity.subject`                  | 工作负载身份的主体                           |
| `identity.<claim>`                  | 一个准确的身份 claim，例如 `user_id`         |
| `instance.agent_instance_id`        | 当前由 Host 管理的 Agent 实例 ID             |
| `instance.parent_agent_instance_id` | 可选的父 Agent 实例 ID                       |
| `instance.delegation_id`            | 可选的委派关联标识                           |
| `instance.actor`                    | 可选的 actor 字符串                          |
| `context.run_id`                    | 当前逻辑 Harness Run ID                      |
| `context.thread_id`                 | 当前 Thread 的 ID                            |
| `context.metadata.<top-level-key>`  | 不可变 `RunBindings.metadata` 中的一个准确值 |

选中的字符串原样发送。JSON 数字、布尔值、对象和数组使用只含有限数值、键排序的紧凑 JSON。例如，`{"region": "us-east", "labels": ["interactive"]}` 会变成 `{"labels":["interactive"],"region":"us-east"}`。值缺失或为 `None` 时，必需绑定会失败，可选绑定则省略。

`headers=` 与 factory 解析结果中的请求头名称，不区分大小写时也不得重叠。`authorization_token`、`allowed_tools`、`description` 和 `defer_loading` 保留上游 MCP 行为。

## 自定义请求头 Factory

请求头需要应用逻辑时，例如查询租户或获取短期凭据，可以使用 factory。它接收可信的 `AgentContext`，以同步或异步方式返回字符串到字符串的映射。

以下构造片段要求 Host 身份含有字符串类型的 `tenant_id` claim。替换服务 URL，并像前面的集成函数一样，将生成的 `mcp` Capability 传给 builder：

```python
from collections.abc import Mapping

from a13n_harness import AgentContext
from a13n_harness.mcp import ContextualMCP


def resolve_mcp_headers(context: AgentContext) -> Mapping[str, str]:
    return {
        "X-Run-ID": context.run_id,
        "X-Agent-Instance-ID": context.instance.agent_instance_id,
        "X-Tenant-ID": context.instance.identity.require_claim("tenant_id"),
    }


mcp = ContextualMCP(
    "https://mcp.example.com/mcp",
    id="tenant-tools",
    headers_factory=resolve_mcp_headers,
    native=False,
    local=True,
)
```

需要异步查询路由时，将 factory 替换为以下函数。`route_store` 是应用自己的服务，`resolve()` 必须返回请求头字符串：

```python
async def resolve_mcp_headers(context: AgentContext) -> Mapping[str, str]:
    route = await route_store.resolve(context.instance.identity)
    return {"X-Route": route}
```

Host 负责路由服务的初始化和清理。两种 factory 都在每个逻辑 Run 中、准备 MCP 工具之前执行一次；由 Host 重建，不保存在可移植状态中。

## 本地与 Provider 原生执行

先确定 MCP 调用在哪里执行，再配置凭据或工具分组：

| 选择     | 参数                       | 行为                                             |
| -------- | -------------------------- | ------------------------------------------------ |
| 本地默认 | `native=False, local=None` | 使用上游基于 URL 的本地 MCP 执行                 |
| 自动     | `native=True, local=None`  | 优先使用 provider 原生 MCP，并采用上游的本地回退 |
| 仅本地   | `native=False, local=True` | 要求本地 URL MCP 执行                            |
| 仅原生   | `native=True, local=False` | 要求 provider 原生 MCP 执行                      |

`ContextualMCP` 接受应用配置的 HTTP(S) URL。预构建客户端、传输对象、进程内服务、脚本或 Toolset 直接使用原生 `MCP`，这些对象已经有自己的连接配置。Provider 原生执行要求模型及 provider 支持该功能。如果 Run 配置了允许访问的主机限制，应使用本地执行，让 Harness 能够实施限制。

## Host 提供的配置

应用可以在自己的配置中暴露服务 URL、稳定 ID、执行方式和上下文请求头绑定，再将这些值解析为现有 MCP API 对象。一个 Agent 选择多个服务时，每个服务使用唯一的 `id`。

持久化不含凭据的期望配置。通过可信 Host 代码重建当前身份、路由查询、请求头和短期凭据。涉及可调用 factory 或当前上下文解析时，使用代码构造 `ContextualMCP`。Harness UI 已有完整的资源格式，见其 [MCP 配置指南](../a13n-harness-ui/mcp.md)。

## 将大型本地 MCP server 的工具分组

服务提供的工具较多时，将本地 `MCP` 或 `ContextualMCP` Capability 本身作为 [ToolSearchGroup 来源](tool-search.md#group-a-run-bound-mcp-capability)。对 Capability 分组，可以保留工具准备之前的 Run 绑定和请求头解析。

选择 `native=False, local=True`，并保持延迟加载关闭。模型通过 ToolSearch 发现 schema，调用仍使用原 MCP Toolset 和传输。这减少了发送给模型的 schema，客户端仍正常初始化来源并读取工具列表。

## 结果边界

本地执行的 MCP 工具将文本和 JSON 结果交给 Harness 函数工具结果边界。过大的值默认会明确截断，不会转存到文件。这个限制针对进入模型历史的数据，不限制客户端接收结果之前的传输体或内存占用。

Provider 原生 MCP 使用 provider 的执行路径，不经过这条本地边界。集成依赖 Harness 本地工具结果处理时，应选择本地执行。
