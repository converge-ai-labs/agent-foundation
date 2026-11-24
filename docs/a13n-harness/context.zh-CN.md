---
title: 上下文与工作状态
description: 注入动态运行时与工作区上下文，管理跨轮次持久化的工作笔记与任务清单，并自动压缩超长对话历史。
---

上下文能力（Context Capabilities）负责装配每次模型请求，工作状态（Working State）跨轮次保存结构化任务与笔记，而历史压缩（Compaction）则确保长会话始终保持在模型的上下文窗口之内。

## 选择合适的机制

| 需求                                 | 推荐机制                                                                 |
| ------------------------------------ | ------------------------------------------------------------------------ |
| 注入当前时间、用量或运行元数据       | `RuntimeContextCapability`                                               |
| 注入工作区目录概览或项目 `AGENTS.md` | `WorkspaceOutlineCapability` 与 `FileContextCapability`                  |
| 跨轮次维护任务清单与工作笔记         | [工作状态](#working-state)（`WorkingStateCapability`）                   |
| 缩短长对话历史                       | `CompactionCapability`、`HandoffCapability` 或 `executable.compact(...)` |
| 从模型提供方上下文窗口溢出中自动恢复 | 上下文恢复（默认开启）                                                   |
| 跨多个独立会话共享长期知识           | [文件记忆或记录记忆](memory.md)                                          |

## 内置与自定义上下文注入

可在 `HarnessBuilder` 上直接挂载内置工作区上下文能力，或继承 `AbstractModelContextCapability` 自定义注入块（配合 `request.history.matches_latest(block)` 仅在内容变化时追加，保护缓存命中率）：

```python
from a13n_harness.capabilities import (
    FileContextCapability,
    RuntimeContextCapability,
    WorkspaceOutlineCapability,
)
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

`FileContextCapability` 在每次逻辑运行中读取工作区根目录的 `AGENTS.md`，并扫描直接子目录中的 `AGENTS.md` 与 `.agents/skills` 入口提示。

## 工作状态

`WorkingStateCapability` 将结构化笔记与任务清单保存在 `HarnessState` 中，使其跨越轮次、检查点与历史压缩持久保留，同时避免膨胀原始消息历史。需要跨不同会话共享信息时请改用[记忆](memory.md)。

## 运行配置与主机白名单

通过 `RunBindings` 传入 `RunConfiguration`，在工具与插件之间共享扩展配置及 HTTP(S) 目标主机白名单：

### 主机规则与正则表达式

`allowed_hosts=None` 允许访问任意主机；空集合禁止访问所有主机。条目按规范化完整主机名执行 `re.fullmatch`，支持精确域名或 `regex:` 正则模式：

```python
from a13n_harness import RunBindings, RunConfiguration

configuration = RunConfiguration(
    allowed_hosts={
        "api.vendor.example",
        r"regex:(api|docs)\.example\.com",
        r"regex:(?:[a-z0-9-]+\.)*assets\.example\.com",
    },
    extensions={"example.reader": {"images": True}},
)
bindings = RunBindings.embedded(configuration=configuration)
```

## 请求过滤器

请求过滤器仅转换发往模型的请求视图，不修改已保存的消息历史：

- **冷启动过滤器（Cold-start filter）：** 在空闲间隔后自动裁剪较早历史工具返回中的超长字符串（已读取的 `SKILL.md` 豁免裁剪）。
- **图像预处理过滤器：** 强制执行 `model_characteristics.image_input` 限制（图片数量、保留比例、Base64 字节上限与 GIF 策略）。

## 压缩长会话历史

可按需组合运行内自动压缩、智能体主动交接总结或宿主在轮次间主动触发的压缩：

```python
from a13n_harness.capabilities import (
    CompactionCapability,
    CompactionPolicy,
    HandoffCapability,
)

# 1. 运行期间自动压缩（默认在达到 context_window_tokens 的 90% 时触发）：
compaction = CompactionCapability(CompactionPolicy(trigger_tokens=100_000))

# 2. 向智能体暴露 summarize 主动交接工具（默认在 65% 时注入提醒）：
handoff = HandoffCapability()

# 3. 宿主在两次运行之间对已落盘检查点直接执行压缩：
# compacted = await executable.compact(previous_state=saved_state)
```

生成摘要替换旧历史后，Harness 会自动保留当前工作笔记与任务清单并落入压缩后的 `HarnessState`（同时发出 `CompactionSummaryEvent`）。在后台异步执行压缩的宿主也可将 `compaction_result` 连同 `previous_state` 一并传给 `run()` 或 `stream()`。
