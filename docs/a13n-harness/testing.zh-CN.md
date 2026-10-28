---
title: 测试 Harness 应用
sidebarTitle: 测试
description: 为输出、保存的历史、流式执行和应用集成建立离线测试。
---

先写三个离线测试：返回答案、恢复已保存的 Thread、消费事件流。再为应用实际使用的工具和资源补充测试。确定性的 Model 让测试运行快，也让失败容易复现。

## 从 `FunctionModel` 开始

使用[快速开始](getting-started.md)准备的仓库和 Python 环境。在仓库根目录安装锁定的开发依赖，其中包含 pytest 和 AnyIO：

```bash
uv sync --locked --package a13n-harness --group dev
```

新建 `test_agent.py`，写入以下完整模块。`anyio_backend` fixture 显式选择 asyncio，因此测试不依赖本仓库的测试配置。

```python title="test_agent.py"
from collections.abc import AsyncIterator

import pytest
from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del info
    turns = 1 + sum(isinstance(message, ModelResponse) for message in messages)
    yield f"turn-{turns}"


def build_agent():
    return HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )


async def test_agent_returns_expected_output() -> None:
    result = await build_agent().run("First turn")

    assert result.output_or_raise() == "turn-1"
    assert result.state is not None
```

将 `test_agent.py` 放在仓库根目录，然后运行：

```bash
uv run python -m pytest -q test_agent.py
```

预期结果为 `1 passed`。这个 Model 统计历史中已有的响应，不调用 provider；Harness 的构建、执行、输出校验和状态导出仍按正常路径运行。

Harness 使用流式 Model 请求。应传入 `FunctionModel(stream_function=...)`；仅提供非流式 `function` 无法运行这条路径。产出字符串模拟文本，产出 `DeltaToolCalls` 模拟确定性的工具调用。[工具与依赖](tools-and-dependencies.md)提供了完整的工具调用示例。

## 明确测试继续执行

将以下测试追加到同一文件。它把完整状态保存到临时文件，重新构建 executable，并检查下次 Model 请求是否能看到两轮输入。

```python
from pathlib import Path

from a13n_harness import HarnessState


async def test_saved_state_resumes_a_rebuilt_agent(tmp_path: Path) -> None:
    first = await build_agent().run("First turn")
    first.raise_for_status()
    assert first.state is not None
    checkpoint = tmp_path / "thread.json"
    checkpoint.write_text(first.state.model_dump_json(), encoding="utf-8")

    restored = HarnessState.model_validate_json(
        checkpoint.read_text(encoding="utf-8")
    )
    second = await build_agent().run("Second turn", previous_state=restored)

    assert second.output_or_raise() == "turn-2"
    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id
```

再次运行同一条 pytest 命令，预期为 `2 passed`。在应用测试中，还要调用应用实际使用的保存和读取函数。验证应用的提交规则：失败或放弃的轮次，不应替换应用打算保留的检查点。

## 将流作为有作用域的资源来测试

把第三个测试追加到 `test_agent.py`：

```python
from a13n_harness import HarnessRunResultEvent


async def test_stream_delivers_a_terminal_result() -> None:
    terminal = None
    async with build_agent().stream("First turn") as stream:
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                terminal = item.result

    assert terminal is not None
    assert terminal.output_or_raise() == "turn-1"
    assert terminal.state is not None
```

同一条命令现在会运行全部三个测试。用 `async with` 包住整个消费过程，确保正常结束、异常和提前退出时都能关闭 Run 资源。应用向调用方转发事件流时，还应测试调用方提前退出能否关闭应用封装层及其临时资源。

## 保护 Provider 提示词的前缀

对于修改 Model 消息的 Capability，测试一次包含多个模型请求的完整 Run：

1. 让 Model 先请求工具，再返回最终答案。
2. 使用公开消息 codec 记录两次请求收到的消息。
3. 将第一次的完整消息序列与第二次的等长前缀比较。
4. 检查最终转换也保留在结果历史中。

这样能发现仅修改发出请求、却未写入规范历史的实现。如果功能本就需要替换历史，则断言准确的替换结果。对于 Capability 指令，记录每次请求的 `AgentInfo.instructions`，检查其保持稳定或发生预期变化。

## 区分定义与执行时的权限

把稳定行为和工具定义构建到 executable 中。与生产代码一样，通过新的 Run 绑定提供用户相关策略、模型路由，以及 Web、文档、媒体或交互协作对象。

每项工具集成都要检查 Model 可见的工具集合，以及协作对象实际收到的类型化请求。应用负责授权规则时，加入一个拒绝操作的测试。参照[工具与依赖](tools-and-dependencies.md#supply-a-live-application-dependency)，确定各个 fake 应放在哪一层。

## 分三层测试 Environment 集成

选择能证明目标行为的最低成本测试层级：

| 层级     | 准备                                     | 检查内容                       |
| -------- | ---------------------------------------- | ------------------------------ |
| 单元测试 | fake 的类型化 Environment 接口或协作对象 | 参数、返回值、策略、错误       |
| 本地集成 | Direct Local 与临时目录                  | 真实文件和进程操作、清理       |
| 隔离集成 | Local Envd 与已构建的 daemon             | EIP 协商、隔离、生命周期、恢复 |

Direct Local 测试 Host 文件和进程访问。需要验证原生隔离或 Environment Interaction Protocol（EIP）时，使用 Local Envd。在已准备好 Envd 开发环境的仓库 checkout 中运行：

```bash
make local-envd-test
```

provider 准备步骤见 [Environment 示例](../environments/examples.md)。启动 daemon 的测试与上面的三个离线测试分开运行。

## 将插件作为已安装的包来测试

在隔离虚拟环境中安装扩展，再测试 entry point 发现。覆盖显式选择、无效配置、每次 Run 的新实例、清理和失败处理。直接修改内存中的目录，会跳过本项测试要验证的打包行为。

[插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)包含已安装 entry point 和直接代码组合两种方式的离线测试。

## 保留一个真实 Provider 的冒烟测试

添加一个小型、按需启用的测试，检查凭据、配置的 Model ID 和网络访问。断言应用的输出契约，而不是精确的生成措辞。将它与离线测试分开，让日常开发无需 provider 账号。

## 仓库示例

应用超出这三个入门测试的范围后，可以参考：

- [Agent Application 测试](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app/tests)：多轮执行、重启、失败、提前退出流和临时 Environment 清理。
- [Harness 测试](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-harness/tests)：公开事件、状态、Capabilities、Environment、观测、委派和插件。
