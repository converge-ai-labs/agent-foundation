---
title: 客户端工具
description: 让客户端应用执行模型请求的操作，再将结果交回 Harness 继续执行。
---

操作需要由另一个应用完成时，可以使用客户端工具，例如打开编辑器文档、操作浏览器，或使用客户端凭据调用外部集成。你在 Harness 中声明工具名和参数。模型请求工具后，Run 挂起，Host 将请求交给客户端，再携带客户端结果启动新的 Run。

在 Run 内执行的 Python 函数见[工具与依赖](tools-and-dependencies.md)。内置提问表单见[人机协作工具](human-in-the-loop.md)；`ask_user_question` 已有自己的声明。

## 离线声明、挂起与恢复

先按[快速开始](getting-started.md)准备 Python 3.13 环境，将以下代码保存为 `client_demo.py`。示例使用确定性模型和模拟的客户端结果，无需凭据或浏览器。

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
    assert len(requests.calls) == 1 and not requests.approvals
    call = requests.calls[0].target_call
    name = client_tool_name(call, requests.metadata.get(call.tool_call_id, {}))
    assert name == "open_document"
    assert call.args_as_dict() == {"document_id": "guide"}

    # A real Host authenticates the client and validates/persists its result here.
    results = requests.build_results(calls={call.tool_call_id: {"opened": True}})
    resumed = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, results),
    )
    assert resumed.run_id != first.run_id
    assert resumed.thread_id == first.thread_id
    assert resumed.output_or_raise() == "The client action finished."
    print(resumed.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

在该环境中运行：

```bash
uv run --no-sync python client_demo.py
```

预期输出：

```text
The client action finished.
```

断言检查交接的两端：客户端收到 `open_document(document_id="guide")`，模型收到 `{"opened": True}`。恢复后的 Run 使用新 ID，但仍属于同一个 Thread。

可以分三部分理解这个示例：

1. 构建 Agent 时，`ClientToolsCapability` 安装工具声明。声明包含给模型的说明，不包含执行回调。
2. 第一次执行返回检查点和完整的待处理批次。客户端调用放在 `requests.calls` 中，审批请求属于另一类别。
3. `build_results()` 用目标调用 ID 关联反馈。`DeferredToolResume` 将反馈交给新的 Run，并搭配先前状态和新的绑定继续执行。

接入真实客户端时，将模拟结果替换为请求投递和结果收集。执行操作前，应按已接受的 schema 校验参数：外部工具 schema 用于指导模型，Harness 不会替客户端执行本地函数参数校验。根据应用的恢复需求，持久化待处理批次、检查点、实际采用的声明和已接受的结果。[状态与恢复](state-and-resume.md)说明了完整批次反馈和持久化顺序。

如果客户端操作失败，将成功结果的 `results` 赋值替换为原生失败对象：

```python
from pydantic_ai import ToolFailed

results = requests.build_results(
    calls={call.tool_call_id: ToolFailed("The client could not open the document.")}
)
```

仍通过同一个 `DeferredToolResume` 路径传回结果。测试此变体前，先调整示例模型中检查成功结果的断言。失败也是外部调用的反馈，不是审批决定。

## 定义与单次执行的选择

如果每次 Run 都使用同一套客户端接口，将工具集固定在 `default_toolsets` 中。如果 Host 需要为每次 Run 选择客户端接口，则开启覆盖：

| 字段                 | 默认值  | 用途                              |
| -------------------- | ------- | --------------------------------- |
| `default_toolsets`   | `()`    | 未传入 Run 覆盖值时使用的声明     |
| `allow_run_override` | `False` | 允许 Host 为单次 Run 替换这些声明 |

设置 `allow_run_override=True` 后，通过 `RunBindings.embedded(client_toolsets=(declaration,))` 传入替换元组。元组会替换整份列表。`client_toolsets=()` 清空列表，`None` 保留默认值。绑定依赖定义中的 `ClientToolsCapability`，不能改变其覆盖策略。Harness 会为本次 Run 校验并复制选定的声明。

为待处理批次保留当时完整的有效声明，并在恢复时再次提供。Harness 检查调用 ID、类别，以及当前工具的名称和类型。Host 则比较保存的声明或摘要：仅凭消息历史，无法确认同名工具仍使用原来的 schema 或执行方。

## 声明字段与限制

`ClientToolDefinition` 的必填字段为 `name`、`description` 和 `parameters_json_schema`，可选字段为 `instruction`、JSON `metadata` 和 `permission`。schema 必须声明 `type: "object"`。通过描述和指令说明模型何时应请求该操作，以及如何填写参数。

`permission` 可设为 `inherit`（默认值）、`allow` 或 `deny`。客户端工具的 `inherit` 提供 `allow` 默认值，Agent 的 ToolPermissions 配置可以覆盖它。外部工具不支持最终生效的 `ask` 和 `review` 模式；客户端确认应由 Host 的投递流程实现。

| 项目                | 限制                      |
| ------------------- | ------------------------- |
| 工具集数 / 工具总数 | 32 / 128                  |
| 工具集 ID / 工具名  | 各 256 个字符             |
| 描述 / 可选指令     | 各 16,384 个字符          |
| 参数 schema         | 编码后的 JSON 最多 64 KiB |
| 元数据              | 编码后的 JSON 最多 16 KiB |

每个工具集至少包含一个工具。工具集 ID 和工具名在实际采用的客户端列表中必须唯一。工具名需匹配 `[A-Za-z_][A-Za-z0-9_.:-]*`。声明会复制 JSON 数据，调用方之后修改原始输入不会改变已选定的值。

将凭据、活动连接、回调和授权保留在 Host 中。元数据是可移植、面向模型的数据；校验会拒绝 Harness 保留键和携带权限的键，包括嵌套的凭据、授权和策略声明。指令用于解释行为，不会强制执行审批。

## 权限与子执行

Host 负责认证执行方、校验操作与结果，并管理确认、幂等性、持久化待处理记录、超时和取消。依据最终的挂起结果以及 Host 已接受的记录启动客户端操作。仅有工具调用观测事件，还不能认定存在待处理命令。进程内授权是另一条边界，见[受管理的工具](managed-tools.md)。

子执行的定义启用了客户端工具，且其 Host 支持反馈生命周期时，子执行也可以使用客户端工具。父执行的客户端声明不会自动继承。Host 为子执行提供新的绑定，并用该子执行自己的待处理批次恢复它。

无法收集反馈的 Host 应设置 `RunBindings.deferred_tools_supported=False`。Harness 会移除声明为延迟执行的工具，拒绝动态延迟请求，并在意外出现最终延迟结果时报告 `deferred_tools_unsupported`。[内置内联执行](delegation-and-codeact.md#host-managed-feedback)使用此模式。

## 通过 Proxy 发现客户端工具

客户端接口较大时，可以通过 [ToolSearch](tool-search.md) 为工具分组。继续修改 `client_demo.py`：加入以下导入，并用这段配置替换 `main()` 中的 builder 代码。保留已有的 `client_tools` 实例。

```python
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection

# Inside main(), after creating client_tools:
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

将 `model_stream()` 中的 `DeltaToolCall` 条目替换为以下内容。确定性模型已经知道 schema；一般模型会先发现工具或读取其完整描述。

```python
0: DeltaToolCall(
    name="call_tool",
    json_args=(
        '{"group":"workspace","tool":"open_document",'
        '"arguments":{"document_id":"guide"}}'
    ),
    tool_call_id="external-1",
)
```

再次运行 `uv run --no-sync python client_demo.py`，预期输出不变。保存的目标名称现在是 `workspace__open_document`，而已有投递代码中的 `client_tool_name()` 仍返回 `open_document`。保留原始目标调用，用其 `tool_call_id` 关联结果。

`toolset_id` 标识客户端来源，ToolSearch 分组控制工具发现。待处理元数据只保留路由标记中的 `declared_name` 和 `toolset_id`，不复制声明中的任意元数据。将完整待处理批次与检查点一起保存。成功值和原生 `ToolFailed` 都使用同一个目标 ID。原生内联等待处理器也可以将调用和对应元数据传给 `client_tool_name()`。客户端声明支持 ToolSearch 分组，但不支持任意改名 wrapper。
