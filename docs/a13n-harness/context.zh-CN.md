---
title: 上下文与工作状态
description: 注入动态运行时上下文，管理持久化工作笔记与任务列表，并自动压缩长对话历史。
---

上下文能力负责拼装每次模型请求，工作状态在多轮对话间保存结构化任务与笔记，上下文压缩则让长对话始终保持在模型上下文窗口内。

## 选择合适的机制

| 需求                         | 机制                            |
| ---------------------------- | ------------------------------- |
| 当前时间、用量或运行元数据   | 运行时上下文                    |
| 工作区结构或项目 `AGENTS.md` | 工作区概览与文件上下文          |
| 跨轮次的任务列表与决策笔记   | `HarnessState` 中的工作状态     |
| 缩短长对话历史               | 交接或主动压缩                  |
| 模型上下文窗口溢出后恢复     | 上下文恢复（默认开启）          |
| 跨线程共享的持久化知识       | [文件记忆或记录记忆](memory.md) |

## 上下文组合

实现 `AbstractModelContextCapability`（或传入 `RunBindings.model_context`）注入请求块。使用 `request.history.matches_latest(block)` 避免重复发送未变化的静态上下文：

```python
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextPlacement,
    ModelContextProjection,
)


class WorkspaceGuide(AbstractModelContextCapability):
    async def wrap_model_context(self, ctx, request, handler):
        projection = await handler(request)
        block = ModelContextBlock(
            source_id="example.guide",
            key="workspace",
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            content="Workspace guidance: run focused tests before committing.",
        )
        if request.history.matches_latest(block):
            return projection
        return ModelContextProjection(blocks=(*projection.blocks, block))
```

## 工作状态

`WorkingStateCapability` 将结构化笔记与任务列表保存在 `HarnessState` 中，跨轮次、检查点与上下文压缩持久保留，同时不膨胀原始消息历史。需要跨线程共享知识时，请使用[记忆](memory.md)。

## 运行配置与主机规则

通过 `RunBindings` 传入 `RunConfiguration`，在工具与插件间共享配置和 HTTP(S) 主机白名单：

```python
from a13n_harness import RunBindings, RunConfiguration

configuration = RunConfiguration(
    allowed_hosts={"api.example.com", "docs.example.com"},
    extensions={"example.reader": {"images": True}},
)
bindings = RunBindings.embedded(configuration=configuration)
```

### 主机规则与正则表达式

`allowed_hosts=None` 允许所有主机；空集合拒绝所有主机。规则对归一化后的完整主机名做全匹配（`re.fullmatch`），支持精确主机名与 `regex:` 模式：

```python
configuration = RunConfiguration(
    allowed_hosts={
        "api.vendor.example",
        r"regex:(api|docs)\.example\.com",
        r"regex:(?:[a-z0-9-]+\.)*assets\.example\.com",
    },
)
```

## 过滤器

请求过滤器仅在发出模型请求前转换内容，不修改已保存的历史记录：

- **冷启动过滤器：** 在空闲一段时间后裁剪较旧工具结果中的超长字符串（已读取的 `SKILL.md` 除外）。
- **图像预处理过滤器：** 按 `model_characteristics.image_input` 限制图片数量、保留比例、单张 base64 字节上限及 GIF 策略。

## 后台压缩

当线程接近 `compact_threshold` 时，主动压缩会自动总结较早轮次并发出 `CompactionSummaryEvent`。宿主也可在带外执行压缩，并在下次调用 `run()` 或 `stream()` 时将 `compaction_result` 与 `previous_state` 一并传入。
