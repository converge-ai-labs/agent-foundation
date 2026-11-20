---
title: 工具与依赖
description: 为 Agent 挂载带类型约束的 Python 函数工具，借助 RunContext 优雅注入业务依赖，并精细管控重试策略与输出上限。
---

Agent 通过**工具（Tools）**与物理世界和业务系统交互。在 Harness 中，任何带有标准类型注解的 Python 函数或异步协程均可直接暴露为 Agent 工具。Harness 底层基于 Pydantic 自动生成符合 JSON Schema 规范的工具描述，在调用前自动校验模型入参，并将执行结果规范封装回传。

## 离线运行函数工具

无需依赖外部大模型 API，借助确定性的 `FunctionModel` 即可在本地秒级验证工具绑定与参数传递：

```python title="tools_demo.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import Tool


# 1. 定义具备明确类型约束的 Python 工具函数
def double(value: int) -> int:
    """返回输入整数的两倍。"""
    return value * 2


# 2. 模拟确定性发起 double(4) 调用的模型
async def mock_tool_model(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str | ToolCallPart]:
    del info
    has_tool_call = any(
        isinstance(part, ToolCallPart)
        for msg in messages
        if isinstance(msg, ModelResponse)
        for part in msg.parts
    )
    if not has_tool_call:
        yield ToolCallPart(tool_name="double", args={"value": 4})
    else:
        # 从前序消息中提取工具返回结果
        return_val = next(
            part.content
            for msg in messages
            if hasattr(msg, "parts")
            for part in msg.parts
            if isinstance(part, ToolReturnPart)
        )
        yield f"计算得到的两倍结果是 {return_val}。"


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(instructions="在收到请求时对数字执行翻倍计算。"),
        output_type=str,
        model=FunctionModel(stream_function=mock_tool_model),
        tools=[Tool(double)],
    )

    result = await executable.run("请计算 4 的两倍")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

使用 `uv run python tools_demo.py` 运行：

```text
计算得到的两倍结果是 8。
```

Harness 自动编排了中间的交互生命周期：模型发起工具调用请求，Harness 调度并执行 `double(4)`，将计算返回值 `8` 规范打包回填至上下文，并继续驱动模型产出最终文本。

## 访问当前 Run

当工具内部需要感知当前的执行上下文——如当前执行的 `run_id`、会话 `thread_id` 或模型请求元数据时，只需声明一个类型为 `RunContext` 的入参即可：

```python
from pydantic_ai import RunContext


def audit_logger(ctx: RunContext, event_name: str) -> str:
    """记录与当前运行绑定的审计事件。"""
    run_id = getattr(ctx, "run_id", "local_run")
    return f"已在 Run [{run_id}] 下成功记录事件 '{event_name}'"
```

Harness 在调度工具时会自动注入当前的上下文句柄；该参数对模型透明，绝不会出现在暴露给模型的 JSON Schema 中。

## 提供活跃的应用依赖

在实际业务系统中，工具往往需要依赖运行时活跃的系统句柄，例如数据库连接池、外部 HTTP 客户端或当前租户上下文对象：

```python
from dataclasses import dataclass
from pydantic_ai import RunContext


@dataclass
class DatabaseConnection:
    connection_string: str

    async def query_user(self, user_id: str) -> dict:
        return {"user_id": user_id, "name": "Alice", "role": "admin"}


# 通过泛型在工具入参中声明所需的业务依赖类型
async def lookup_user(ctx: RunContext[DatabaseConnection], user_id: str) -> dict:
    """从业务数据库中查询用户档案详情。"""
    db = ctx.deps
    return await db.query_user(user_id=user_id)
```

在发起单次运行调用时，通过 `RunBindings` 动态注入依赖实例：

```python
from a13n_harness import RunBindings

db_pool = DatabaseConnection(connection_string="postgresql://localhost:5432/app")

result = await executable.run(
    "查询 user_123 的用户信息",
    bindings=RunBindings.embedded(deps=db_pool),
)
```

这一依赖注入设计保证了 Agent 的静态定义能够跨请求共享复用，同时允许不同请求安全绑定各自独立的数据库连接或特定租户凭证。

## 工具执行路径

Harness 支持三大类工具执行模式：

| 工具类别 | 执行通道 | 典型应用场景 |
| :--- | :--- | :--- |
| **原生进程工具** | 进程内直接调用的异步 Python 原生函数 | 业务数据库查询、数值计算、本地数据转换 |
| **执行环境工具** | 调度至沙箱文件系统或 Docker 容器执行 | 源码审查、文件批量修改、Shell 脚本构建 |
| **远程 MCP 工具** | 基于 HTTP/SSE 或 Stdio MCP 标准协议通信 | SaaS 企业应用互联、GitHub、Slack、知识库检索 |

## 错误、重试与输出限制

借助多层保护屏障保障宿主系统稳定，避免模型上下文被异常冲垮：

- **参数校验防御：** 若模型给出了不符合类型契约的入参，Harness 会自动拦截并生成清晰的校验提示，协助模型在下一轮尝试中自我纠错。
- **大输出自动截断：** 如果工具输出了海量数据（如误查出一万行数据），为避免撑爆上下文窗口，可通过 `max_content_bytes` 自动进行安全截断，并将完整原始内容另存为临时文件。
- **重试上限限制：** 为工具调用配置最大重试次数预算，防止下游服务不可用时引发无休止的模型死循环。

## 下一步

1. [受管工具与权限审批](managed-tools.md)：在执行敏感变更类工具前要求必须获得人工授权。
2. [Model Context Protocol (MCP)](mcp.md)：通过标准协议即插即用各类外部工具服务。
3. [委派与 CodeAct](delegation-and-codeact.md)：让 Agent 通过 Python 并发代码高效调度与编排多个工具。
