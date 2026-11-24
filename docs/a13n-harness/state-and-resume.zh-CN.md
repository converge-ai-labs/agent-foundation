---
title: 状态与恢复（State and Resume）
description: 保存对话检查点，将 HarnessState 序列化为 JSON，分叉线程，并在人工审批后安全恢复挂起的智能体运行。
---

`HarnessState` 是可移植的 Pydantic 检查点，封装了线程的 `thread_id`、完整消息历史以及各能力的隔离状态（如工作笔记、CodeAct `store` 键值与子线程谱系），同时将凭据、网络连接与宿主策略严格留在 `RunBindings` 中。

## 延续、序列化与分叉线程

```python title="continue_thread.py"
import os
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState

executable = HarnessBuilder().build(
    AgentSpec(model=os.environ["HARNESS_MODEL"]),
    output_type=str,
)

# 第 1 轮 -> 序列化为 JSON 存入 PostgreSQL / SQLite / 磁盘
first = await executable.run("Remember code 8492.")
saved_json = first.state.model_dump_json()

# 第 2 轮 -> 反序列化并以新的 run_id 在同一 thread_id 上继续对话
restored = HarnessState.model_validate_json(saved_json)
second = await executable.run("What code did I give you?", previous_state=restored)

# 或分叉出拥有独立 thread_id 的新分支，原线程不受影响
branch = restored.fork()
forked_result = await executable.run("Try an alternate approach.", previous_state=branch)
```

在流式消费期间（`async with executable.stream(...) as stream:`），可随时调用 `await stream.export_state()` 获取中间安全检查点，或在资源清理完成后读取终态 `HarnessRunResultEvent.result.state`。

## 结构化挂起

当工具触发审批（`ask`）或等待外部/人工输入（`ask_user_question`、客户端工具）时，当前 Run 会以 `result.status == "suspended"` 干净结束，并返回 `result.state` 与 `result.deferred`（包含 `deferred.approvals` 与 `deferred.calls`）。

### 恢复延迟调用与中断保护

将 `result.state` 与 `result.deferred` 一并持久化。收集完全部审批和回答后，通过 `DeferredToolResume` 在新 Run 中恢复执行（若流式恢复在首个模型步提交前意外中断，可将导出的检查点与 `stream.pending_deferred_input` 配对重放）：

```python
from a13n_harness import DeferredToolResume

answers = first.deferred.build_results(
    approvals={approval_call_id: True},
    calls={question_call_id: {"answers": {"Which scope?": "Focused"}}},
)
resumed = await executable.run(
    previous_state=first.state,
    deferred_resume=DeferredToolResume(first.deferred, answers),
)
```

## 恢复未应答的工具调用

若进程在工具执行中途崩溃并从中断检查点恢复，Harness 会依据工具声明的幂等属性（如 `HarnessToolMetadata(idempotency="read_only")`）与恢复策略进行对账——仅在最新权限下重跑已声明安全的只读/幂等调用，并将非幂等调用标记为已中断，交由模型先核查外部状态。

## 宿主检查点机制

在多 Worker 生产环境中：

1. 使用线程级租约/栅栏（Lease/Fence）保护检查点写入，防止过期 Worker 覆盖更新的进度。
2. 在向客户端确认完成前，在同一数据库事务中原子写入 `result.state`、环境状态、`MemoryCursors` 与待处理 `deferred` 信封。
3. 当运行失败（`result.status == "failed"`）时，检查 `result.failure` 并决定是采用候选 `result.state` 还是回退到上一轮检查点。
