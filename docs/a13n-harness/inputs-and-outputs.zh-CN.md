---
title: 输入与输出
description: 强约束类型安全的 Pydantic 或 JSON Schema 输出、传入多模态二进制输入、在进入环境后动态构造提示词，并处理所有运行终态。
---

在构建智能体时声明输出契约，向每次运行传入文本或多模态输入，并在发布结果前显式处理运行终态。

## 返回强类型或 JSON Schema 输出

将以下离线示例保存为 `output_example.py` 并运行 `uv run python output_example.py`：

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
    print(output.model_dump_json())  # {"summary":"No findings","files_checked":3}


if __name__ == "__main__":
    asyncio.run(main())
```

构建时二选一配置输出契约（`output_type` 与 `AgentSpec.output_schema` 互斥）：

- **Python 类型或 `OutputSpec`（`output_type=ReviewSummary` 或 `output_type=str`）：** 返回经过校验的 Python 实例。
- **配置驱动的 JSON Schema（`AgentSpec(output_schema=...)` 且 `output_type=None`）：** 校验并返回 `dict[str, JsonValue]`。

## 处理所有运行终态

脚本可直接调用 `result.output_or_raise()`（或 `result.raise_for_status()`），交互式宿主则应按 `result.status` 显式分支：

```python
result = await executable.run("Complete the task")

if result.status == "completed":
    output = result.output_or_raise()
elif result.status == "suspended":
    pending = result.deferred  # 保存 result.state 并通过 DeferredToolResume 恢复
elif result.status == "failed":
    failure = result.failure
else:
    pass  # "cancelled"：切勿将半截文本当作成功业务结果
```

检查点持久化与消息视图（`result.all_messages()` / `result.new_messages()`）见[状态与恢复](state-and-resume.md)，流式事件（`async with executable.stream(...)`）见[智能体与运行](agents-and-runs.md#stream-events)，AG-UI 投影见[流协议](../a13n-stream-protocol/index.md)。

## 传入多模态内容或在进入环境后生成输入

普通文本可直接传入字符串；使用支持多模态的模型时可组合传入 `BinaryContent`（若需由智能体自主读取工作区媒体文件，请参阅[多模态理解](multimedia-understanding.md)）：

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

当输入内容依赖已进入的执行环境或刚分配的 `run_id` 时，传入 `input_factory` 替代 `input`：

```python
from a13n_harness import RunPreparationContext


async def make_input(preparation: RunPreparationContext) -> str:
    return f"Return the review summary for Run {preparation.run_id}."


result = await executable.run(input_factory=make_input)
```
