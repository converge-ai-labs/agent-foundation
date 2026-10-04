---
title: 输入与输出
description: 用文本和媒体开始或恢复一次执行，并处理经过校验的输出及所有结束状态。
---

输入用于开始或恢复一次逻辑执行。输出是执行完成后，应用获得的、经过校验的值。工具返回值、进度事件、摘要和保存的状态各有用途，不能与最终输出混为一谈。

Harness 使用 Pydantic AI 原生的输入和输出类型，并提供统一的结果结构，让应用区分完成、挂起、失败和取消。

## 返回结构化结果

以下完整离线示例会校验一个 Pydantic 模型。在[快速入门中克隆的仓库目录](getting-started.md)中，将它保存为 `output_example.py`，再运行 `uv run python output_example.py`。

```python
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic import BaseModel, Field
from pydantic_ai.models.test import TestModel


class ReviewSummary(BaseModel):
    summary: str
    files_checked: int = Field(ge=0)


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(instructions="Return a bounded review summary."),
        output_type=ReviewSummary,
        model=TestModel(
            custom_output_args={"summary": "No findings", "files_checked": 3}
        ),
    )
    result = await executable.run("Review the supplied changes.")
    output = result.output_or_raise()
    assert isinstance(output, ReviewSummary)
    assert output.files_checked == 3
    print(output.model_dump_json())


if __name__ == "__main__":
    asyncio.run(main())
```

示例模型只提供测试数据，并不会检查文件。Pydantic AI 校验输出后，Harness 会将结果与状态、用量、关联标识和继续执行所需的状态一并返回。

## 构建时确定输出约定

通过 `output_type` 传入 Python 类型或 Pydantic AI 原生的 `OutputSpec`。也可以设置 `AgentSpec.output_schema`，用 JSON Schema 定义字典输出。两种方式不能同时使用。

输出约定属于执行对象，不能在每次执行时改变。应用需要不同的输出约定时，应构建另一个执行对象。原生输出函数和输出模式沿用 Pydantic AI 的校验与重试行为，Harness 不另设一套输出解析机制。

## 处理所有结束状态

```python
result = await executable.run("Complete the task")

if result.status == "completed":
    output = result.output_or_raise()
    # The application decides whether and how to publish this value.
elif result.status == "suspended":
    pending = result.deferred
    # Persist an accepted checkpoint and ask the appropriate human/client.
elif result.status == "failed":
    failure = result.failure
    # Report the safe failure and inspect any available checkpoint.
else:
    # Cancelled: do not treat partial text as a successful business result.
    pass
```

只接受成功完成时，可以调用 `raise_for_status()`；还需要获取指定类型的输出时，使用 `output_or_raise()`。挂起的执行需要[提交延迟结果后恢复](state-and-resume.md)，不能直接重新提交原始提示词。

最终结果表示清理结束后，进程内得到的候选结果，**并不证明** 数据库已提交或客户端已收到。持久化和交付仍由应用自己的策略决定。

## 文本与多模态输入

普通文本直接传入字符串：

```python
result = await executable.run("Explain the change")
```

Pydantic AI 原生的用户内容序列可以同时携带文本和受支持的媒体。以下片段要求当前实际使用的模型支持图像，且应用能够读取图片文件：

```python
from pathlib import Path
from pydantic_ai import BinaryContent

result = await executable.run(
    [
        "Describe this screenshot.",
        BinaryContent(data=Path("screenshot.png").read_bytes(), media_type="image/png"),
    ]
)
```

文件访问、大小限制和模型兼容性由嵌入 Harness 的应用负责。如果 Agent 要读取获准访问的环境文件，请使用[环境多媒体理解](multimedia-understanding.md)，避免应用文件路径隐含地赋予模型访问权限。

## 进入环境后再生成输入

输入依赖已进入的当前环境或刚分配的执行标识时，可以使用 `input_factory`：

```python
from a13n_harness import RunPreparationContext


async def make_input(preparation: RunPreparationContext) -> str:
    return f"Inspect the workspace for Run {preparation.run_id}."


result = await executable.run(input_factory=make_input, environment=environment)
```

`input` 和 `input_factory` 不能同时使用。工厂在模型执行前调用一次。Host 必须提供新的环境对象；导入状态不会重建客户端或凭据。

## 延续历史或流式获取进度

传入 `previous_state=first.state` 可以在线程中继续执行。要支持重启恢复，应持久化完整的 `HarnessState`，而不只是界面显示的文本。`all_messages()` 返回结果的完整独立消息历史，`new_messages()` 只返回本次执行新增的消息。

使用 `async with executable.stream(...)` 可以逐步接收公开事件。文本增量表示进度，不能代替最终结果。父级流中还可能出现关联到其他执行的子事件，不能把子执行的结束事件误判为根执行完成。

流的管理职责见 [Agent 与执行](agents-and-runs.md#stream-events)，AG-UI 映射见 [Stream Protocol](../a13n-stream-protocol/index.md)。
