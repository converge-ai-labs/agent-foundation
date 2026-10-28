---
title: 输入与输出
description: 用文本和媒体开始或恢复一次执行，并处理经过校验的输出及所有结束状态。
---

让应用直接获得可以使用的带类型结果：构建 Agent 时定义输出，执行时传入文本或媒体，发布输出前先处理执行状态。

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

预期输出：

```json
{"summary":"No findings","files_checked":3}
```

`TestModel` 提供固定数据，便于你在没有凭据时测试应用。`output_type=ReviewSummary` 定义要校验的值，`output_or_raise()` 只在执行成功完成后返回这个值。要实际进行审查，请换成[已配置的模型](models.md)，再通过输入或获授权工具提供待审查的变更。

## 构建时确定输出约定

文本输出用 `output_type=str`，结构化值用 `ReviewSummary` 这样的 Python 类型。要使用 Pydantic AI 的输出模式或输出函数，可传入原生 `OutputSpec`。输出约定属于执行对象；需要另一种约定时，构建另一个执行对象。

如果应用从配置加载 schema，使用 `AgentSpec.output_schema`。在示例中尝试这条路径时，用下面的代码替换构建调用，并设置 `output_type=None`：

```python
executable = HarnessBuilder().build(
    AgentSpec(
        instructions="Return a bounded review summary.",
        output_schema=ReviewSummary.model_json_schema(),
    ),
    output_type=None,
    model=TestModel(
        custom_output_args={"summary": "No findings", "files_checked": 3}
    ),
)
```

再将输出断言和打印语句替换为：

```python
assert isinstance(output, dict)
assert output["files_checked"] == 3
print(output)
```

schema 路径返回经过校验的字典，而不是 `ReviewSummary` 实例。`output_type` 和 `output_schema` 必须且只能选择一个。校验和输出重试沿用 Pydantic AI；Harness 还会按声明的 JSON Schema 校验最终字典值。

## 处理所有结束状态

只接受成功结果的脚本可以直接用 `output_or_raise()`。交互式应用则需要为每种状态设置分支。在 `main()` 中，可用下面的模式替换执行和打印部分：

```python
result = await executable.run("Complete the task")

if result.status == "completed":
    output = result.output_or_raise()
    # The application decides whether and how to publish this value.
elif result.status == "suspended":
    pending = result.deferred
    # Save result.state and collect answers for these deferred requests.
elif result.status == "failed":
    failure = result.failure
    # Report the safe failure and inspect any available checkpoint.
else:
    # Cancelled: do not treat partial text as a successful business result.
    pass
```

只接受成功完成时，可以调用 `raise_for_status()`；还需要获取指定类型的输出时，使用 `output_or_raise()`。挂起的执行需要[提交延迟结果后恢复](state-and-resume.md)，不能直接重新提交原始提示词。

清理完成后的结果包含状态、用量、Thread 和 Run 标识，以及可用的续接状态。随后由应用保存结果并交付给客户端。是否已持久化、客户端是否已收到，取决于这些应用步骤是否完成。

## 文本与多模态输入

下面的片段放在 `main()` 中，沿用同一个 `executable`。普通文本直接传入字符串：

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

这里由应用读取文件并提供字节。应用需要检查文件访问权限、大小限制和模型支持情况。如果要让 Agent 自行选择并读取获授权环境中的文件，请配置[环境多媒体理解](multimedia-understanding.md)。

## 进入环境后再生成输入

输入依赖已进入的环境或刚分配的执行标识时，使用 `input_factory`。下面只读取标识，可以直接用于离线示例。将导入和函数放在模块顶层，再替换 `main()` 中的 `executable.run(...)` 调用：

```python
from a13n_harness import RunPreparationContext


async def make_input(preparation: RunPreparationContext) -> str:
    return f"Return the review summary for Run {preparation.run_id}."


result = await executable.run(input_factory=make_input)
```

工厂在环境进入后、模型执行前调用一次。要在工厂中检查工作区，按[环境工具](environments.md)的方式传入新的 `environment=` 入口，并使用 `preparation.environment`。`input` 和 `input_factory` 只能传一个。恢复的状态携带对话数据；当前客户端和凭据由 Host 提供。

## 延续历史或流式获取进度

要在同一线程中继续示例，在第一个结果成功返回后添加：

```python
follow_up = await executable.run(
    "Return the review summary again.", previous_state=result.state
)
assert follow_up.thread_id == result.thread_id
assert follow_up.run_id != result.run_id
assert follow_up.output_or_raise() == result.output_or_raise()
```

固定测试数据让两次输出相等。真实模型可以根据之前的对话作答。要支持重启恢复，按[状态与恢复](state-and-resume.md)持久化完整的 `HarnessState`。`all_messages()` 返回结果全部历史的独立副本，`new_messages()` 返回本次执行新增的消息。

使用 `async with executable.stream(...)` 逐步接收公开事件，离开作用域后读取 `stream.result`。文本增量用于展示进度，最终结果用于取得应用输出。父级流中可能出现子事件；应按 Run 关联事件，不能将子执行的结束事件当成根执行完成。

流的管理职责见 [Agent 与执行](agents-and-runs.md#stream-events)，AG-UI 映射见 [Stream Protocol](../a13n-stream-protocol/index.md)。
