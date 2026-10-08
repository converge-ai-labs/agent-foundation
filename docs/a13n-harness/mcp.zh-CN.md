---
title: MCP 工具
description: 连接 MCP server 提供的工具，并发送根据当前身份或 Run 生成的请求头。
---

使用 MCP 连接其他进程或服务的工具。`MCP` 管理连接配置；`ContextualMCP` 用于随 Run 变化的请求头。

| 需求                                                        | 选择                                             |
| ----------------------------------------------------------- | ------------------------------------------------ |
| 静态 server、stdio 进程、进程内 server 或预先构建的 Toolset | 原生 `MCP`                                       |
| 根据当前 Harness 身份或 Run 生成 URL server 的请求头        | `ContextualMCP`                                  |
| Harness UI 中的命令或 JSON server 配置                      | [Harness UI MCP 配置](../a13n-harness-ui/mcp.md) |

SDK 代码通过 MCP Capability 接入；Harness UI 使用 MCP 资源配置。

## 离线运行 MCP 工具

在[源码环境](getting-started.md#requirements)中，将以下代码保存为 `mcp_demo.py`，执行 `uv run python mcp_demo.py`。示例启动进程内 server，两次调用 `add` 工具，无需 provider 凭据。

```python title="mcp_demo.py"
import asyncio
from collections.abc import AsyncIterator

from fastmcp import Client, FastMCP
from pydantic_ai.capabilities import MCP
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from a13n_harness import AgentSpec, HarnessBuilder, RunBindings

server = FastMCP("counter")


@server.tool
def add(a: int, b: int) -> int:
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
            print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

两次 Run 都输出 `Result: 8`。Host 保持已进入的客户端，每次 Run 使用新的 `MCPToolset` 投影。

## 原生 MCP

将原生 `MCP` 加入 `AgentSpec.capabilities` 或 builder 的 `capabilities=` 参数。

可以直接从 AgentSpec 文档重建本地执行（`local: True`）的 URL server：

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

基础安装已包含本地 URL 和 stdio MCP 运行时。进程内 server、传输对象、脚本路径或预构建的 `MCPToolset` 需要在 Python 中构造 `MCP`。设置 `defer_loading=True`，可通过 `load_capability` 发现该 Capability；设置 `native=True, local=False`，由选定的模型 provider 执行 URL server。

## Host 持有的客户端

多个 Run 需要使用同一服务器状态时，可信 Host 代码可以持有已进入的 FastMCP 客户端，为每个 Run 创建新的上游投影：

```python
from fastmcp import Client
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset

from a13n_harness import RunBindings


async def use_host_client(executable):
    async with Client("https://mcp.example.com/mcp", mode="auto") as client:
        results = []
        for prompt in ("Create a workspace", "Inspect that workspace"):
            projection = MCPToolset(client, id="workspace", cache_tools=False)
            bindings = RunBindings.embedded(
                capabilities=(MCP(id="workspace", local=projection),)
            )
            results.append(await executable.run(prompt, bindings=bindings))
        return results
```

进入客户端前配置认证和输入处理器。`mode="auto"` 选择现代发现或旧版协商，也可显式选择 `legacy` 和 `2026-07-28` 模式。FastMCP 管理多轮输入和请求状态。客户端仅在相同身份与请求头下共享，每次 Run 创建新投影。业务调用丢失响应时，先检查 server 上的结果再重复调用。

## 用 `ContextualMCP` 生成当前 Run 的请求头

URL server 的请求头需要包含当前用户、Thread 或 Run 时，使用 `ContextualMCP`。Harness 在连接工具前，每次 Run 解析一次请求头。

常见的身份、关联关系、Run 和元数据值，可使用声明式解析器：

```python
from a13n_harness import (
    AgentIdentityRef,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)

mcp = ContextualMCP(
    "https://mcp.example.com/mcp",
    id="knowledge",
    native=True,
    local=None,
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
    agent_spec,
    output_type=str,
    model=model,
    capabilities=(mcp,),
)

bindings = RunBindings.embedded(
    identity=AgentIdentityRef(
        issuer="my-host",
        subject="support-agent",
        user_id="user-123",
        agent_id="agent-support",
    ),
    metadata={
        "request_context": {
            "region": "us-east",
            "labels": ["interactive", "priority"],
        }
    },
)
result = await executable.run("Find the account record", bindings=bindings)
```

将附加 JSON 值放入 `RunBindings.metadata`；`context.metadata.<key>` 选择一个准确的顶层键。

声明式解析器只支持以下准确的来源：

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

声明式解析器的来源不足时，可以使用自定义同步或异步 factory。它接收逻辑 Run 的完整可信 `AgentContext`，返回准确的字符串到字符串映射：

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
    native=True,
    local=None,
)
```

异步 factory 使用相同的输入输出约定：

```python
async def resolve_mcp_headers(context: AgentContext) -> Mapping[str, str]:
    route = await route_store.resolve(context.instance.identity)
    return {"X-Route": route}
```

Factory 每次 Run 运行一次，内部模型恢复复用该请求头快照。新 Run 会重新解析请求头。

## 本地与 Provider 原生执行

`ContextualMCP` 只接受基于 URL 的上游执行：

| 选择     | 参数                       | 行为                                             |
| -------- | -------------------------- | ------------------------------------------------ |
| 本地默认 | `native=False, local=None` | 使用上游基于 URL 的本地 MCP 执行                 |
| 自动     | `native=True, local=None`  | 优先使用 provider 原生 MCP，并采用上游的本地回退 |
| 仅本地   | `native=False, local=True` | 要求本地 URL MCP 执行                            |
| 仅原生   | `native=True, local=False` | 要求 provider 原生 MCP 执行                      |

预构建客户端、传输对象、进程内 server、脚本和预构建 Toolset 已自行管理连接设置。这些值直接使用原生 `MCP`，不要与 `ContextualMCP` 组合。

`ContextualMCP` 要求应用配置 HTTP(S) URL。

## Host 提供的配置

Host 可以通过自身的可信配置模型暴露同一条 URL 接入路径。保留 `ContextualMCP` 字段及准确的执行选择，不要再实现另一套 MCP 运行时。只持久化不含凭据的期望配置；构建 Capability 时，通过进程内 factory 解析请求头、短期凭据和当前路由。

每个 server 的 `id` 唯一时，一个 Agent 可以选择多个 MCP server。如果配置需要 callable factory、当前身份、静态请求头或独立的秘密解析器，应通过代码构建 `ContextualMCP`。

## 将大型本地 MCP server 的工具分组

在 `ToolProxyCapability(groups=...)` 中，将本地 `MCP` 或 `ContextualMCP` Capability 作为 [ToolProxyGroup 来源](tool-proxy.md#group-a-run-bound-mcp-capability)，可以暴露分组发现接口，而不是所有工具 schema。选择 `native=False, local=True`；provider 原生工具和延后加载来源不能作为代理目标。原生组合保留新的 Run 绑定和上下文请求头，调用仍使用原 MCP Toolset 和传输。这减少的是模型上下文，不减少 MCP 初始化或工具列表读取工作。

## 结果边界

本地执行的 MCP 工具是普通的动态发现函数工具。它们返回的文本和 JSON 经过 Harness 必需的结果边界，过大时默认明确截断，而非写入外部文件。限制针对进入模型历史的值，不限制 MCP 客户端接收结果之前的传输体或进程内存。Provider 原生 MCP 执行仍走 provider 路径，不经过本地函数工具边界。
