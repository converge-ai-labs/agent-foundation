---
title: 测试 Harness 应用
sidebarTitle: 测试
description: 使用确定性模型，测试 Agent 的流式输出、状态和 Environment 边界。
---

测试 Harness 应用时，应覆盖 Harness 向应用暴露的边界：确定性模型行为、终结结果、继续执行的状态、公开流事件、Environment 生命周期，以及选用的扩展。依赖 provider 网络的测试应与快速应用测试分开。

## 从 `FunctionModel` 开始

Pydantic AI 的 `FunctionModel` 无需 API key，就能运行真实的 Agent 和 Harness 执行路径：

```python
from collections.abc import AsyncIterator

import pytest
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


async def respond(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str]:
    del messages, info
    yield "expected output"


async def test_agent_returns_expected_output() -> None:
    model = FunctionModel(stream_function=respond)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model,
    )

    result = await executable.run("Complete the task")

    assert result.output_or_raise() == "expected output"
    assert result.state is not None
```

这个测试覆盖了 Harness 构建、Pydantic AI 执行循环、终结结果的规范化，以及状态导出。Harness 发出流式模型请求，因此 `FunctionModel` 需要 `stream_function`；只传 `FunctionModel(function=...)` 会使 Run 失败。产出字符串可模拟文本增量；需要测试工具调用、延后交互或 provider 的历史消息格式时，产出 `DeltaToolCalls`。

## 明确测试继续执行

继续同一个 Thread 时，`thread_id` 保持不变，`run_id` 则会重新生成：

```python
first = await executable.run("First turn")
second = await executable.run(
    "Second turn",
    previous_state=first.state,
)

assert second.thread_id == first.thread_id
assert second.run_id != first.run_id
```

测试应用的持久化逻辑时，应使用应用实际采用的序列化器和存储，将完整的 `HarnessState` 保存后再读取。还应确认失败或被放弃的执行不会覆盖最后一次已提交的检查点。

## 保护 Provider 提示词的前缀

当 Capability 会修改模型可见的消息时，应在同一次 Run 中至少发起两次模型请求。记录 Model 收到的消息。通过公开的消息 codec，将每次请求的完整消息序列与下一次请求中等长的前缀比较。确认最终转换后的内容保留在规范的结果历史中。这样可以发现仅作用于某次请求、却没有写回当前历史的转换。如果 Capability 的设计就是要替换历史，应断言准确的状态变化，而不是放宽或省略前缀检查。

如果 Capability 还会提供指令，应记录每次请求的 `AgentInfo.instructions`，并确认它们完全相同。如果约定允许指令变化，则断言预期的具体变化，不要放宽稳定性检查。

## 将流作为有作用域的资源来测试

在异步上下文中消费流，并确认收到了终结结果：

```python
from a13n_harness import HarnessRunResultEvent

items = []
terminal = None
async with executable.stream("Stream the answer") as stream:
    async for item in stream:
        items.append(item)
        if isinstance(item, HarnessRunResultEvent):
            terminal = item.result

assert items
assert terminal is not None
terminal.raise_for_status()
```

如果应用向调用方暴露流式接口，还应测试消费者提前退出的情况。此时应用仍必须关闭流的作用域，释放临时 Environment 资源和当前 Run 的协作对象。

## 区分定义与执行时的权限

用固定的 fake 对象测试定义中选定的 Capabilities。针对具体用户的策略、模型路由，以及媒体、文档、Web 或交互协作对象，则通过新的 Run 绑定注入。这样测试才与生产环境的职责边界一致，也能防止凭据或可变权限进入可复用的定义。

如果 Capability 暴露工具，两端都需要测试：

1. 模型只能看到预期的工具接口；
2. 协作对象收到预期的类型化请求和策略上下文。

## 分三层测试 Environment 集成

| 层级     | 使用方式                                   | 验证内容                                                              |
| -------- | ------------------------------------------ | --------------------------------------------------------------------- |
| 单元测试 | fake 的类型化 Environment 接口或协作对象   | 工具参数、结果转换、策略和错误                                        |
| 本地集成 | Direct Local Provider 配合临时目录         | 真实的文件与进程行为，以及资源清理                                    |
| 隔离集成 | Local Envd 配合已构建的 `a13n-envd` 二进制 | Environment Interaction Protocol（EIP）协商、隔离前提、生命周期和恢复 |

Direct Local 不能替代隔离环境。需要验证 EIP 或原生隔离边界的测试，应使用真实的 Local Envd 执行路径。

在此仓库中，可以运行对应的 Local Envd 集成检查：

```bash
make local-envd-test
```

## 将插件作为已安装的包来测试

测试 entry point 发现时，应在隔离的 Python 虚拟环境中构建或安装扩展分发包，而不是直接修改目录。验证显式选择、配置校验、每次 Run 都创建新实例、资源清理和故障隔离。

仓库的[插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)提供了 entry point 和直接代码接入两种路径，都有离线测试。

## 保留一个真实 Provider 的冒烟测试

可以提供一个小型、按需启用的冒烟测试，验证 provider 凭据、模型命名和网络集成。主测试集不应依赖在线模型。除非 provider 和 seed 能保证响应确定，否则不要对模型响应做精确匹配；应验证应用约定的行为。

## 仓库示例

- [Agent 应用测试](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app/tests)覆盖多轮执行、重启恢复、失败、提前退出流，以及临时 Environment 的清理。
- [Harness 测试](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-harness/tests)展示了如何围绕公开边界，测试事件、状态、Capabilities、Environment、观测、委派和插件。
