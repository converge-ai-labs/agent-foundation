---
title: 客户端工具
description: 声明由客户端应用在 Harness 执行之外执行的工具，再携带结果恢复执行。
---

模型可以请求某项操作，但必须由另一个应用执行时，可以使用客户端工具。例如浏览器操作、外部应用集成，或需要认证的其他客户端。声明提供模型所需的说明和 JSON 参数 schema，不包含 Python 执行函数或凭据。

支持延迟工具的执行会挂起，并返回带有关联标识的外部调用。Host 负责认证执行方、执行操作或收集其结果、持久化结果，再携带 `DeferredToolResume` 启动新的执行。进程内的函数工具见[工具与依赖](tools-and-dependencies.md)。内置 `ask_user_question` 的请求和答案结构见[人机协作工具](human-in-the-loop.md)；无需自定义客户端工具声明。

## 离线声明、挂起与恢复

下面的完整示例使用确定性模型和模拟的外部结果，不请求 provider，也不执行浏览器操作：

```python
import asyncio

from a13n_harness import DeferredToolResume, HarnessBuilder, RunBindings
from a13n_harness.tools import (
    ClientToolDefinition,
    ClientToolsCapability,
    ClientToolsetDefinition,
    ClientToolsSpec,
)
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
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model_stream),
        capabilities=(
            ClientToolsCapability(
                spec=ClientToolsSpec(default_toolsets=(declaration,))
            ),
        ),
    )
    first = await executable.run("Open the guide", bindings=RunBindings.embedded())
    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None
    requests = first.deferred
    call = requests.calls[0]
    assert call.tool_name == "open_document"

    # A real Host authenticates the client and validates/persists its result here.
    results = requests.build_results(calls={call.tool_call_id: {"opened": True}})
    resumed = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, results),
    )
    assert resumed.output_or_raise() == "The client action finished."
    print(resumed.output_or_raise())


asyncio.run(main())
```

1. `ClientToolsCapability` 在**定义** 中配置，负责确定实际可用的工具范围。
2. 模型请求工具后，Harness 不会在进程内代为执行应用操作。
3. `first.deferred.calls` 提供精确的调用标识；`build_results()` 保留调用关联，并校验结果类别。
4. 恢复时使用选定的状态和**新的绑定**，而不是一直保持开启的流，也不是随意追加一条用户消息。

完整结果反馈、审批类别、状态选择和清理见[状态与恢复](state-and-resume.md)。只序列化检查点，并不意味着外部命令的执行结果已被持久化。

## 定义与单次执行的选择

`ClientToolsSpec` 包含两个字段：

| 字段                 | 默认值  | 含义                                     |
| -------------------- | ------- | ---------------------------------------- |
| `default_toolsets`   | `()`    | 定义中配置的默认工具声明                 |
| `allow_run_override` | `False` | 是否允许 Host 为一次执行替换完整工具范围 |

定义明确允许覆盖时，可以传入 `RunBindings.client_toolsets`。这是**整份列表替换**，不会合并：`client_toolsets=()` 清空可用工具，`None` 保留默认值。绑定不能安装 `ClientToolsCapability`，也不能改变其 `allow_run_override` 策略。有效的工具声明会经过校验，并为本次执行复制一份。

使用稳定的 `toolset_id` 和工具名。恢复外部调用时，仍须匹配选定的声明及当前继续执行的约定；不能通过更改 schema 或替换执行方，接受不匹配的待处理结果。

## 声明字段与限制

`ClientToolDefinition` 包含 `name`、`description`、`parameters_json_schema`，以及可选的 `instruction` 和 JSON `metadata`。参数 schema 必须声明 `type: "object"`。指令只是对模型的指导，不会强制实施审批策略。声明不会继续引用调用方可修改的原始输入。

| 项目                | 限制                      |
| ------------------- | ------------------------- |
| 工具集数 / 工具总数 | 32 / 128                  |
| 工具集 ID / 工具名  | 各 256 个字符             |
| 描述 / 可选指令     | 各 16,384 个字符          |
| 参数 schema         | 编码后的 JSON 最多 64 KiB |
| 元数据              | 编码后的 JSON 最多 16 KiB |

每个工具集至少包含一个工具。工具集 ID 和实际可用的工具名不能重复，名称还需满足声明的标识符格式。元数据不接受 Harness 保留键，也不接受携带权限的键，例如凭据、授权和策略。不要在可移植声明中放入运行中的客户端、认证信息、可执行回调或服务端授权声明。

## 权限与子执行

审批界面、执行方认证、命令幂等性、持久化的待处理记录、超时与取消策略，以及结果接收，都由 Host 负责。客户端提供的元数据不能授权服务端工具。独立的进程内调用边界见[受管理的工具](managed-tools.md)。

当前 Host 支持结果反馈生命周期时，子执行可以延迟等待。没有这项生命周期支持的 Host 应设置 `RunBindings.deferred_tools_supported=False`：声明为延迟执行的工具会被移除，动态延迟请求会被拒绝，意外出现的最终延迟结果会以 `deferred_tools_unsupported` 失败。不要根据观测事件创建等待任务，应保留最终结果中完整、精确的待处理批次和检查点。[内置内联执行](delegation-and-codeact.md#host-managed-feedback)禁用延迟工具；支持此功能的 Host 则直接恢复自己管理的子执行。
