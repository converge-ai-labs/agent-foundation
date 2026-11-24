---
title: 测试 Harness 应用
sidebarTitle: 测试
description: 使用 FunctionModel、TestModel 与三级环境验证策略，为 Harness 智能体编写快速、确定性的单元与集成测试。
---

先从三个快速离线测试起步——返回回答、恢复已保存会话、消费事件流——再针对实际使用的工具、执行环境与插件补充测试。确定性测试模型无需联网凭据即可保证结果可复现。

## 使用 `FunctionModel` 编写核心离线测试套件

安装开发依赖（`uv sync --locked --package a13n-harness --group dev`），保存 `test_agent.py` 并运行 `uv run python -m pytest -q test_agent.py`：

```python title="test_agent.py"
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, HarnessRunResultEvent, HarnessState
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


async def test_saved_state_resumes_a_rebuilt_agent(tmp_path: Path) -> None:
    first = await build_agent().run("First turn")
    assert first.state is not None
    checkpoint = tmp_path / "thread.json"
    checkpoint.write_text(first.state.model_dump_json(), encoding="utf-8")

    restored = HarnessState.model_validate_json(checkpoint.read_text(encoding="utf-8"))
    second = await build_agent().run("Second turn", previous_state=restored)

    assert second.output_or_raise() == "turn-2"
    assert second.thread_id == first.thread_id and second.run_id != first.run_id


async def test_stream_delivers_a_terminal_result() -> None:
    terminal = None
    async with build_agent().stream("First turn") as stream:
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                terminal = item.result

    assert terminal is not None and terminal.output_or_raise() == "turn-1"
```

由于 Harness 发起流式模型请求，请始终传入 `FunctionModel(stream_function=...)`（按需产出 `str` 分片或 `DeltaToolCalls`，示例见[工具与依赖](tools-and-dependencies.md)）。同时请测试宿主真实的存取逻辑，确保失败或取消的轮次不会覆盖已提交检查点，并验证消费方提前退出 `async with executable.stream(...)` 时的资源清理。

## 验证能力扩展、提示词前缀与运行期绑定

- **提示词前缀稳定性：** 若某项能力会改写模型消息，请执行多步请求测试（先调工具再给最终回答），断言首轮消息序列与次轮等长前缀完全一致且保存在规范历史中，以保护模型提供商的 Prompt 缓存命中率。
- **定义与运行期权限分离：** 将静态指令与工具构建进可执行体，通过全新的 `RunBindings` 注入单次请求协作者（参见[工具与依赖](tools-and-dependencies.md#supply-a-live-application-dependency)），同时断言可见 Schema 与权限拒绝分支。
- **已安装插件发现：** 在安装了扩展包的独立虚拟环境中测试 Entry Point 发现机制（参考 [`examples/plugins`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)）。

## 分三级验证执行环境并保留冒烟测试

选择能够证明目标行为的最轻量环境层级：

| 层级       | 测试配置                                            | 验证重点                               |
| ---------- | --------------------------------------------------- | -------------------------------------- |
| 单元测试   | 伪造类型化环境端口或协作者                          | 参数传递、返回值、权限策略与错误处理   |
| 本地集成   | 搭配 `tmp_path` 的 Direct Local                     | 真实文件/进程读写与目录清理            |
| 隔离层集成 | 本地 `a13n-envd` 守护进程（`make local-envd-test`） | EIP 协议协商、沙箱隔离、生命周期与恢复 |

将守护进程测试与一项可选开启的真实提供商冒烟测试（断言结构化输出 Schema 与业务不变量）同默认离线套件分开维护。完整范例可参考 [`examples/agent-app/tests`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app/tests) 与 [`packages/a13n-harness/tests`](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-harness/tests)。
