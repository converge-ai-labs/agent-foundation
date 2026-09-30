---
title: MCP 工具
description: 连接 MCP server 提供的工具，并根据当前身份或 Run 生成请求头。
---

通过 MCP 接入其他进程或服务提供的工具。Harness 组合 Pydantic AI 的 MCP Capability，不额外实现传输客户端。

| 需求                                                        | 选择                                             |
| ----------------------------------------------------------- | ------------------------------------------------ |
| 静态 server、stdio 进程、进程内 server 或预先构建的 Toolset | 原生 `MCP`                                       |
| 根据当前 Harness 身份或 Run 生成 URL server 的请求头        | `ContextualMCP`                                  |
| 终端产品的命令或 JSON 配置                                  | [Harness UI MCP 配置](../a13n-harness-ui/mcp.md) |

Harness SDK 的 `AgentSpec` **不接受** Harness UI 顶层的 `mcp_servers` 资源字段。SDK 使用 Capabilities，资源 ID 和配置文件由应用管理。

## 原生 MCP

MCP 使用 Pydantic AI 原生的 `MCP` Capability，放在 `AgentSpec.capabilities` 中。Agent Harness 不定义另一套 MCP 客户端、协议、server schema，或与之并列的 `mcp_servers` 字段。

可以直接从 AgentSpec 文档重建本地执行的 URL server：

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

默认安装的 `a13n-harness` 已包含 Pydantic AI 的 MCP 客户端运行时，本地 URL 和 stdio 传输无需额外的 Harness extra。如果需要进程内 server、传输对象、脚本路径或预构建的 `MCPToolset` 等更丰富的进程内输入，在可信代码中构建 `pydantic_ai.capabilities.MCP`，通过定义的 Capability 组合传入。如果 Host 为某次执行持有新建的认证客户端或 toolset，也可将准确的上游 `MCP` 实例附加到 `RunBindings.capabilities`；绝不要跨 Run 复用此认证实例。`defer_loading=True` 使用上游 `load_capability`，仍遵循相同的 Harness 工具边界。需要选定模型 provider 原生执行 URL MCP server 时，使用 `native=True, local=False`。

## 用 `ContextualMCP` 生成当前 Run 的请求头

URL MCP server 的请求头需要根据当前逻辑 Harness Run 生成时，使用 `ContextualMCP`。定义只保存不执行任何操作的 URL 配置。在 Pydantic Capability 的 Run 绑定阶段，它会解析请求头并构建新的上游 `MCP`，之后才提取原生工具或本地 MCP Toolset。

常见的 Identity、关联关系、Run 和元数据值，可使用声明式解析器：

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

`RunBindings.metadata` 用于附加每次 Run 的 JSON 值。先放入准确的顶层键，再通过 `context.metadata.<key>` 选择。不要临时给 `AgentContext` 添加属性，也不要编码嵌套反射路径。

声明式解析器只支持以下准确的来源类别：

| 来源                                | 解析值                                       |
| ----------------------------------- | -------------------------------------------- |
| `identity.issuer`                   | Workload Identity 的签发方                   |
| `identity.subject`                  | Workload Identity 的主体                     |
| `identity.<claim>`                  | 一个准确的 Identity claim，例如 `user_id`    |
| `instance.agent_instance_id`        | 当前由 Host 管理的 Agent 实例 ID             |
| `instance.parent_agent_instance_id` | 可选的父 Agent 实例 ID                       |
| `instance.delegation_id`            | 可选的委派关联标识                           |
| `instance.actor`                    | 可选的 actor 字符串                          |
| `context.run_id`                    | 当前逻辑 Harness Run ID                      |
| `context.thread_id`                 | 当前独立推进的 Thread ID                     |
| `context.metadata.<top-level-key>`  | 不可变 `RunBindings.metadata` 中的一个准确值 |

选中的字符串原样发送。JSON 数字、布尔值、对象和数组使用只含有限数值、键排序的紧凑 JSON。例如，`{"region": "us-east", "labels": ["interactive"]}` 会变成 `{"labels":["interactive"],"region":"us-east"}`。值缺失或为 `None` 时，必需绑定会失败，可选绑定则省略。

`headers=` 与 factory 解析结果中的请求头名称，不区分大小写时也不得重叠。`authorization_token`、`allowed_tools`、`description` 和 `defer_loading` 保留上游 MCP 行为。

## 自定义请求头 Factory

内置选择器不足时，可以使用自定义同步或异步 factory。它接收逻辑 Run 的完整可信 `AgentContext`，返回准确的字符串到字符串映射：

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

Factory 每个逻辑 Harness Run 只运行一次。内部模型恢复尝试复用同一个活跃的上游 MCP 和请求头快照；另一个逻辑 Run 则重新解析。Factory 是可信 Host 代码，可以主动读取当前 Run 服务，但模型内容无法选择 selector，也无法直接调用它。

## 本地与 Provider 原生执行

`ContextualMCP` 只接受基于 URL 的上游执行：

| 选择     | 参数                       | 行为                                             |
| -------- | -------------------------- | ------------------------------------------------ |
| 本地默认 | `native=False, local=None` | 使用上游基于 URL 的本地 MCP 执行                 |
| 自动     | `native=True, local=None`  | 优先使用 provider 原生 MCP，并采用上游的本地回退 |
| 仅本地   | `native=False, local=True` | 要求本地 URL MCP 执行                            |
| 仅原生   | `native=True, local=False` | 要求 provider 原生 MCP 执行                      |

预构建客户端、传输对象、进程内 server、脚本和预构建 Toolset 已自行管理连接设置。这些值直接使用原生 `MCP`，不要与 `ContextualMCP` 组合。

URL 是明确的可信配置。Harness 要求 `ContextualMCP` 使用 HTTP(S) URL，除此之外，URL、传输、授权和 provider 验证都交给上游 MCP 集成。它不会猜测 URL 的各部分是否包含凭据。

## Host 提供的配置

Host 可以通过自身的可信配置模型暴露同一条 URL 接入路径。保留 `ContextualMCP` 字段及准确的执行选择，不要再实现另一套 MCP 运行时。只持久化不含凭据的期望配置；构建 Capability 时，通过进程内 factory 解析请求头、短期凭据和当前路由。

每个 server 的 `id` 唯一时，一个 Agent 可以选择多个 MCP server。如果配置需要 callable factory、当前身份、静态请求头或独立的秘密解析器，应通过代码构建 `ContextualMCP`。

## 对大量本地 MCP 工具分组

在 `ToolProxyCapability(groups=...)` 中，将本地 `MCP` 或 `ContextualMCP` Capability 作为 [ToolProxyGroup 来源](tool-proxy.md#group-a-run-bound-mcp-capability)，可以暴露分组发现接口，而不是所有工具 schema。选择 `native=False, local=True`；provider 原生工具和延后加载来源不能作为代理目标。原生组合保留新的 Run 绑定和上下文请求头，调用仍使用原 MCP Toolset 和传输。这减少的是模型上下文，不减少 MCP 初始化或工具列表读取工作。

## 结果边界

本地执行的 MCP 工具是普通的动态发现函数工具。它们返回的文本和 JSON 经过 Harness 必需的结果边界，过大时默认明确截断，而非写入外部文件。限制针对进入模型历史的值，不限制 MCP 客户端接收结果之前的传输体或进程内存。Provider 原生 MCP 执行仍走 provider 路径，不经过本地函数工具边界。
