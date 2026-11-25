---
title: MCP 工具
description: 为 Harness 智能体接入 Model Context Protocol (MCP) 服务器，复用宿主托管的 FastMCP 客户端，并通过 ContextualMCP 按运行注入身份请求头。
---

通过 MCP 能力为智能体接入外部进程或远程服务的工具：

| 场景需求                                          | 能力 / 辅助类                                      |
| ------------------------------------------------- | -------------------------------------------------- |
| 固定 URL、stdio 命令、进程内服务器或 `MCPToolset` | `pydantic_ai.capabilities.MCP`                     |
| 需要按次注入身份或元数据请求头的 HTTP(S) 服务器   | `a13n_harness.mcp.ContextualMCP`                   |
| 有界初始化重试或可跳过（`on_error="skip"`）服务器 | `a13n_harness.mcp.MCPServer`                       |
| 在交互式工作台中通过 YAML 配置服务器              | [Harness UI MCP 服务器](../a13n-harness-ui/mcp.md) |

## 复用宿主托管的 FastMCP 客户端

在多次 Run 之间保持一个已打开的 `Client`，并在每次 Run 中通过 `RunBindings` 传入全新的 `MCPToolset` 投影：

```python title="mcp_demo.py"
import asyncio
from fastmcp import Client, FastMCP
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings

server = FastMCP("calculator")


@server.tool
def add(a: int, b: int) -> int:
    return a + b


async def respond(messages, info: AgentInfo):
    if isinstance(messages[-1], ModelRequest):
        for part in messages[-1].parts:
            if isinstance(part, ToolReturnPart):
                yield f"Result: {part.content}"
                return
    yield {0: DeltaToolCall(name=info.function_tools[0].name, json_args='{"a": 3, "b": 5}')}


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
    )
    async with Client(server, mode="auto") as client:
        for prompt in ("Add 3 and 5", "Add them again"):
            projection = MCPToolset(client, id="calculator")
            bindings = RunBindings.embedded(capabilities=(MCP(id="calculator", local=projection),))
            result = await executable.run(prompt, bindings=bindings)
            print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

共享客户端应限定在单一身份与请求头配置下使用。状态检查点仅保存对话历史，不保存实时连接。

## 声明式 `MCP` 与执行模式

在 `AgentSpec` 中声明固定 URL 服务器（或将 `MCP(...)` 传给 `HarnessBuilder().build(..., capabilities=...)`）：

```python
from a13n_harness import AgentSpec

agent_spec = AgentSpec.from_dict({
    "capabilities": [{
        "MCP": {
            "url": "https://mcp.example.com/mcp",
            "id": "knowledge",
            "local": True,
            "native": False,
            "allowed_tools": ["search"],
        }
    }]
})
```

| 模式     | 参数                       | 行为                                                      |
| -------- | -------------------------- | --------------------------------------------------------- |
| 本地默认 | `native=False, local=None` | 由 Harness 本地通过 HTTP/stdio 执行 MCP 调用              |
| 强制本地 | `native=False, local=True` | 强制本地执行（启用 `allowed_hosts` 校验时必须选择此模式） |
| 自动选择 | `native=True, local=None`  | 优先使用模型提供方原生 MCP，不支持时回退本地              |
| 强制原生 | `native=True, local=False` | 要求由模型提供方直接调用远程 MCP 服务器                   |

## 使用 `ContextualMCP` 按次注入请求头

当 HTTP(S) 服务器需要当前 Run 的 `AgentContext` 或 `AgentIdentityRef` 字段时，使用 `ContextualMCP`：

```python
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)

mcp = ContextualMCP(
    "https://mcp.example.com/mcp",
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
                    "context.metadata.request_context", required=False
                ),
            }
        )
    ),
)
```

支持的声明式选择器：`identity.issuer`、`identity.subject`、`identity.<claim>`、`instance.agent_instance_id`、`instance.parent_agent_instance_id`、`instance.delegation_id`、`instance.actor`、`context.run_id`、`context.thread_id` 与 `context.metadata.<top-level-key>`（非字符串自动序列化为紧凑有序 JSON）。

如需异步查询租户路由或短期令牌，可向 `headers_factory=` 传入同步或异步函数 `(context: AgentContext) -> Mapping[str, str]`。

如需共享 Harness 进程可见的目录，可通过 `roots=` 传入 `file://` URI 列表或 FastMCP Roots 回调。默认值 `None` 不启用 Roots。该选项仅适用于本地 MCP 客户端，模型提供商的原生 MCP 不会收到这些路径。

## 启动故障隔离与大型工具目录

- **可选服务器与初始化重试：** 使用 `MCPServer(id=..., prepare=..., on_error="skip")` 搭配 `InitializationRetryClient`（瞬态初始化错误最多重试 3 次，带 1s/2s 退避；`skip` 仅在当次 Run 中略过失败服务器而不中断运行）。
- **工具搜索：** 当服务器暴露大量工具时，将本地 `MCP` 或 `ContextualMCP` 实例直接作为 [ToolSearchGroup 来源](tool-search.md#group-a-run-bound-mcp-capability)（`native=False, local=True`），在按需加载 Schema 的同时保留按次请求头解析。
