---
title: 客户端工具
description: 在 Harness 中声明外部客户端工具，在模型调用时挂起运行，并在调用方前端或工作进程返回结果后恢复会话。
---

当操作归属于外部应用（例如打开编辑器文档、操控本地界面或使用客户端持有的凭据调用集成）时，请使用客户端工具。你在 Harness 中声明工具名称与 JSON Schema；模型发起调用时运行挂起，由宿主将请求投递给客户端，并在收到结果后发起恢复运行。

进程内 Python 函数请使用[工具与依赖](tools-and-dependencies.md)；内置结构化提问表单请使用[人机协同工具](human-in-the-loop.md)。

## 离线声明、挂起与恢复

在[快速开始](getting-started.md)环境中将以下离线示例保存为 `client_demo.py`：

```python
import asyncio

from a13n_harness import DeferredToolResume, HarnessBuilder, RunBindings
from a13n_harness.tools import (
    ClientToolDefinition,
    ClientToolsCapability,
    ClientToolsetDefinition,
    ClientToolsSpec,
)
from a13n_harness.tools.client import client_tool_name
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel


async def model_stream(messages, info):
    returns = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    if returns:
        assert returns[-1].content == {"opened": True}
        yield "The client action finished."
    else:
        yield {
            0: DeltaToolCall(
                name="open_document",
                json_args='{"document_id":"guide"}',
                tool_call_id="external-1",
            )
        }


async def main():
    declaration = ClientToolsetDefinition(
        toolset_id="document-client",
        tools=(
            ClientToolDefinition(
                name="open_document",
                description="Open a document in the client application.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"document_id": {"type": "string"}},
                    "required": ["document_id"],
                    "additionalProperties": False,
                },
            ),
        ),
    )
    client_tools = ClientToolsCapability(
        spec=ClientToolsSpec(default_toolsets=(declaration,))
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
        capabilities=(client_tools,),
    )
    first = await executable.run("Open the guide", bindings=RunBindings.embedded())
    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None

    requests = first.deferred
    call = requests.calls[0].target_call
    assert client_tool_name(call, requests.metadata.get(call.tool_call_id, {})) == "open_document"
    assert call.args_as_dict() == {"document_id": "guide"}

    results = requests.build_results(calls={call.tool_call_id: {"opened": True}})
    resumed = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, results),
    )
    assert resumed.thread_id == first.thread_id and resumed.run_id != first.run_id
    print(resumed.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

执行 `uv run --no-sync python client_demo.py`，输出 `The client action finished.`：

1. `ClientToolsCapability` 仅注册面向模型的工具声明，不包含本地执行回调。
2. `first.deferred.calls` 随 `first.state` 一起暴露待处理客户端调用。生产宿主应在执行客户端动作前按 Schema 校验参数，并持久化检查点与待处理批次（参见[状态与恢复](state-and-resume.md)）。
3. `requests.build_results(...)` 按 `tool_call_id` 关联结果，再通过 `DeferredToolResume` 启动续接运行。若客户端执行失败，可传入 `ToolFailed`：

```python
from pydantic_ai import ToolFailed

results = requests.build_results(
    calls={call.tool_call_id: ToolFailed("The client could not open the document.")}
)
```

## 定义、单次运行覆盖与边界限制

可在构建期配置固定声明，或允许宿主按运行动态替换：

| 字段 / 边界             | 取值与行为                                                                                                             |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `default_toolsets`      | 默认为 `()`；未提供运行期覆盖时使用的工具集声明                                                                        |
| `allow_run_override`    | 默认为 `False`；设为 `True` 时可通过 `RunBindings.embedded(client_toolsets=(...,))` 替换（`()` 清空，`None` 保留默认） |
| `permission`            | 支持 `inherit`（默认解析为 `allow`）、`allow` 或 `deny`（不支持 `ask`/`review`，客户端确认由宿主工作流负责）           |
| 工具集 / 工具总数       | 最多 `32` 个工具集、`128` 个工具；ID 与符合 `[A-Za-z_][A-Za-z0-9_.:-]*` 的名称全局唯一（各 `<= 256` 字符）             |
| 描述 / 指令 / JSON 大小 | 描述与指令各 `<= 16,384` 字符；对象类型 `parameters_json_schema` `<= 64 KiB`；可移植 `metadata` `<= 16 KiB`            |

恢复待处理批次时请再次传入一致的生效声明。凭据、活跃连接、用户确认、幂等与超时均由宿主管理，且仅在收到终态 `suspended` 结果后才启动客户端动作。子智能体不会自动继承父运行的客户端声明；无法收集外部反馈的宿主可将 `RunBindings.deferred_tools_supported` 设为 `False`。

## 代理工具发现

大型客户端接口可通过 [ToolSearch](tool-search.md) 按组收敛，同时保持投递路由不变：

```python
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection

search = ToolSearchPlan(
    groups={
        "workspace": ToolSearchSelection(
            "Document actions", capabilities=(client_tools,)
        )
    }
)
executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=model_stream),
    capabilities=(client_tools,),
    tool_search=search,
)
```

当模型调用 `call_tool`（`{"group": "workspace", "tool": "open_document", "arguments": {"document_id": "guide"}}`）时，底层目标调用名变为 `workspace__open_document`，而 `client_tool_name(call, requests.metadata.get(call.tool_call_id, {}))` 仍返回 `"open_document"`。请保持目标调用原样，并统一使用 `call.tool_call_id` 回填成功值或 `ToolFailed`。
