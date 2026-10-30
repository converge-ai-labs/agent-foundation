---
title: 状态与恢复
description: 保存已完成的一轮，重启程序，再继续同一个 Thread。
---

应用接受一轮结果时，保存 `result.state`。下一轮读取该状态，并通过 `previous_state` 传入。Harness 在同一个 Thread 中创建新的 Run；应用重新提供当前 Model、工具和访问策略。

## 继续 Thread

使用[快速开始](getting-started.md)准备的 Python 环境。将这个完整离线程序保存为 `conversation.py`：

```python title="conversation.py"
import argparse
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del info
    turn = 1 + sum(isinstance(message, ModelResponse) for message in messages)
    yield f"turn-{turn}"


async def main(checkpoint: Path, prompt: str) -> None:
    previous = (
        HarnessState.model_validate_json(checkpoint.read_text(encoding="utf-8"))
        if checkpoint.exists()
        else None
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    result = await executable.run(prompt, previous_state=previous)
    answer = result.output_or_raise()
    if result.state is None:
        raise RuntimeError("Completed turn has no continuation state")

    temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
    temporary.write_text(result.state.model_dump_json(), encoding="utf-8")
    temporary.replace(checkpoint)
    print(answer)
    print(f"Thread: {result.thread_id}")
    print(f"Run: {result.run_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("prompt")
    args = parser.parse_args()
    asyncio.run(main(args.checkpoint, args.prompt))
```

在同一目录运行两次，使用一个新的检查点文件名：

```bash
uv run python conversation.py tutorial-thread.json "Draft the plan"
uv run python conversation.py tutorial-thread.json "Review the plan"
```

第一个进程输出 `turn-1`，第二个输出 `turn-2`。两次 Thread ID 相同，Run ID 不同。这个确定性 Model 统计历史中已有的响应，用来检查新构建的 executable 是否收到了保存的历史，不会生成真实计划。

程序只保存已完成的轮次，在序列化成功后替换文件。每个检查点文件只由一个写入方使用。应用支持并发请求或多个 worker 时，应由应用存储层协调检查点的选择和发布；参见 [Host 检查点](#host-checkpointing)。

## 序列化状态

示例使用公开的 JSON 序列化和读取接口：

```python
payload = result.state.model_dump_json()
restored = HarnessState.model_validate_json(payload)
```

这两行假定已按前面的示例检查结果包含状态。保存完整结构。存储接口接受 UTF-8 字节时，也可以使用 `state.to_json_bytes()`。两种方式都会验证公开消息历史，都不会保存 executable 或打开的连接。

## 状态内容

| `HarnessState` 保存的内容         | 应用提供或管理的内容                    |
| --------------------------------- | --------------------------------------- |
| schema 版本和稳定的 Thread ID     | executable 定义及其版本                 |
| 公开的 Model 消息历史             | Model、工具、Capabilities、插件、客户端 |
| 带版本的 JSON Capability 数据     | 当前身份、凭据、策略、审批              |
| 兼容 Environment 挂载的可移植状态 | 挂载选择、目标生命周期、当前授权        |
| 可选 Host state store 产生的引用  | 被引用的字节、保留规则、存储访问        |

默认情况下，新 Run 使用新的绑定、上下文和用量统计。状态携带继续执行所需的数据，Host 重建当前权限。显式延续用量统计见[用量](usage-and-limits.md)。

使用 `RunBindings.state_store` 时，只要选定检查点仍需要引用的数据，就应保留这些数据，并在恢复时绑定兼容存储。未使用 store 时，相关值直接保存在状态内。执行记录、持久异步子任务、交付和长期记忆由各自的 Host 或功能存储负责。

## Fork Thread

用 `fork()` 将已有对话分支为独立推进的 Thread。在已有 `executable` 和已读取状态 `restored` 的异步函数中：

```python
branch = restored.fork()
assert branch.thread_id != restored.thread_id
assert branch.message_history == restored.message_history
assert branch.environment_states == {}

result = await executable.run("Explore another approach", previous_state=branch)
```

将分支保存到另一个应用记录或文件。`fork()` 保留继续执行的内容，为保留的内联子级分配新 Thread 身份，并清空可移植 Environment 状态。它不会复制 Host 管理的异步子级，也不会选择底层资源。

需要应用指定身份时，在创建阶段调用 `HarnessState.new(thread_id=...)`，或在分支阶段调用 `state.fork(thread_id=...)`。将 ID 当作不透明的关联值，分支 ID 必须与来源不同。

## 流式期间导出

大多数应用检查终态结果后再保存即可。需要中间检查点的 Host，可以在活动流中导出：

```python
async with executable.stream("Continue the task", previous_state=restored) as stream:
    async for item in stream:
        candidate = await stream.export_state()
        # Apply the application's checkpoint policy before saving candidate.
```

这个集成片段导出最新的安全公开消息边界，不保存任意 token 增量。Host 决定候选是否仍有效、能否安全发布。导出状态本身不会提交检查点；可选的 state store 可能在导出期间写入被引用的内容。

### 压缩已保存的检查点

Host 可调用 `await executable.compact(previous_state=restored)`，执行一个只生成摘要的 Run。检查结果后再选择返回状态。成功压缩后的检查点可立即保存；下次普通 `run()` 或 `stream()` 会先恢复运行时上下文，再继续任务。这个显式操作不要求 Compaction Capability 或阈值。完整流程和输出模式限制见[上下文管理](context.md)。

## 结构化挂起

审批或外部工具可让 Run 以 `status="suspended"` 结束，同时返回 `state`、`deferred` 请求结构和 `suspend_reason="deferred"`。应用在该 Run 关闭后收集回答，再启动下一次 Run。

仅含审批的流程可按以下顺序集成。此片段假定 `executable` 声明了需要审批的工具，`approvals` 包含应用已收集的决定：

```python
from a13n_harness import DeferredToolResume, RunBindings

first = await executable.run("Perform the action", bindings=RunBindings.embedded())
if first.status != "suspended" or first.state is None or first.deferred is None:
    raise RuntimeError("Expected an approval suspension")

# approvals maps each target tool_call_id to the collected bool/ToolApproved/ToolDenied.
results = first.deferred.build_results(approvals=approvals)
accepted = DeferredToolResume(first.deferred, results)
second = await executable.run(
    previous_state=first.state,
    bindings=RunBindings.embedded(),
    deferred_resume=accepted,
)
```

完整可运行的工具声明和外部结果流程见[客户端工具](client-tools.md)。用户提问见[人工参与](human-in-the-loop.md)。每个待处理项都应在原类别下回答一次；审批映射不能回答外部调用。

将完整的 Harness `DeferredToolRequests` 与对应检查点一起保存。`calls` 或 `approvals` 的每项都包含 `model_call`、`target_call` 和 `target_identity`。展示 `target_call`，以其中的 `tool_call_id` 为回复键；Harness 使用保留的模型调用关联历史。从 `a13n_harness` 导入这些请求类型和 `DeferredToolResume`。

恢复会检查历史关联、当前目标身份、参数 schema 和新策略。`ToolApproved.override_args` 可提供替换参数。审批不会预留资源，也不会覆盖当前授权。Host 管理的子级在绑定支持延后工具时可使用此流程；内置内联子级关闭这项支持。没有延后处理生命周期的 Host 应设置 `deferred_tools_supported=False`。

## 恢复中断的延迟续接

应用已接受的外部输入可能还未进入检查点历史。在历史纳入输入前，将已接受的 `DeferredToolResume` 保存在检查点旁：

1. 应用接受输入时，保留完整请求和结果。
2. 恢复执行的事件流中，将当前检查点与 `stream.pending_deferred_input` 配对。
3. 一起发布这两份数据。剩余输入为 `None` 时，清除保存的已接受批次。
4. 中断后读取两份记录，通过 `recovery=True` 恢复。

`stream.pending_deferred_input` 在流关闭后仍可读取。它在历史变换前移除已消费的输入，因此压缩不会让旧输入重新生效。始终与当前规范检查点配对，不要搭配旧快照。对于独立保存的批次和未经变换的匹配历史，`accepted.remaining(history)` 可以计算未回答的剩余项。

下面的片段假定 `checkpoint` 和可选的 `accepted` 已从应用存储读取：

```python
recovery_input = (
    DeferredToolResume(accepted.requests, accepted.results, recovery=True)
    if accepted is not None
    else None
)
result = await executable.run(
    previous_state=checkpoint,
    bindings=RunBindings.embedded(),
    deferred_resume=recovery_input,
    tool_recovery="declared",
)
```

即使 `tool_recovery="never"`，恢复也会采纳保留的外部结果、失败和拒绝，并跳过已写入历史的结果。之前的批准不代表允许重放结果不确定的本地动作；未解决的操作仍遵循当前恢复策略和审批规则。

## 恢复未回答工具调用

崩溃后缺少结果，意味着操作结果可能未知。先按前节提供已保留的接受输入，再为其余未回答的历史调用选择每次 Run 的 `tool_recovery` 策略：

| 模式                 | 行为                                               |
| -------------------- | -------------------------------------------------- |
| `"declared"`（默认） | 重放显式标为可恢复重试的工具；其他调用记录未知结果 |
| `"never"`            | 为所有未回答调用记录未知结果                       |
| `"always"`           | 重放当前工具集合中仍可用的未回答调用               |

已记录的结果仍有效，包括已有的未知结果和参数重试结果。已移除的工具得到未知结果。这些模式不改变新生成的调用，也不保证外部副作用恰好发生一次。

### 在来源声明可重试工具

只有在先前操作可能已完成、重复仍可接受时，才标记该工具。例如只读查询可以写为：

```python
from a13n_harness.tools import recovery_retryable
from pydantic_ai.capabilities import Capability


@recovery_retryable
def lookup_status(record_id: str) -> str:
    """Read a status from the demonstration catalog."""
    return {"item-1": "ready"}.get(record_id, "unknown")


catalog = Capability(id="catalog", tools=[lookup_status])
```

将 `catalog` 传给 builder 的 `capabilities` 参数。辅助函数返回原生 `Tool`，也可复制已有 Tool 并保留其设置。对于实例方法，在构建 Capability 时标记已绑定方法。动态 Toolset 可用相同方式标记当前工具，也可在工具元数据中将 `RECOVERY_RETRY_SAFE_METADATA_KEY` 设为布尔 `True`。

声明从当前工具定义读取。它独立于 provider 传输重试和 `HarnessToolMetadata.idempotency`：仅有幂等键，不能证明重放会复用原来的上游操作。

### 按策略恢复

在已有选定 `checkpoint` 和当前 executable 的异步函数中：

```python
result = await executable.run(previous_state=checkpoint, tool_recovery="declared")
```

Harness 在下一次 Model 请求前处理恢复的调用。当前参数校验、审批要求、外部执行要求和受管理调用策略仍适用。新的完整 `DeferredToolResume` 直接提供收集到的批次；中断后的延后恢复使用 `recovery=True` 和选定策略。

## 安全的失败状态候选

失败结果可能携带规范化状态，保留可见文本和已完成的推理，去除不适合重放的未完成片段。应用按自己的恢复策略决定是否选择该状态。重复结果不确定的修改操作前，应检查外部系统，或使用该操作已有的幂等约定。

`RunCleanupError.outcome` 同样是结果不确定的候选，因为正常终态交付没有完成。在核实结果前，将它与应用最后提交的检查点分开管理。

## Capability 状态

Capability 将可移植数据保存在稳定 ID 和确切版本下。在可访问当前 `AgentContext`（命名为 `context`）的 hook 或工具中：

```python
from pydantic import BaseModel


class CounterState(BaseModel):
    value: int


current = await context.state.read("acme.counter", CounterState, version="1")
await context.state.write(
    "acme.counter",
    CounterState(value=(current.value if current else 0) + 1),
    version="1",
)
```

状态 API 校验并复制 JSON 数据。所属 Capability 解释版本；未读取的命名空间可以原样跨 Run 保留。凭据、客户端、锁和 Environment 生命周期数据仍交由各自的集成管理。

## Shell 执行与观测

可移植状态与实时进程控制具有不同的生命周期：

- 独立的 `process-*` 引用属于一个 Harness Run。
- Host 的 `ToolExecutionRuntime` 持有顶层 Shell 执行时，可在同一 Thread 的多个 Run 之间保留 `exec-*` 引用，前提是该 runtime 仍持有执行。
- Host 重启后，provider 支持的发现操作可能找到保留的命令并分配新引用。加载消息历史不会重建旧 runtime。

Run 关闭会释放未被保留的观测；命令是否存活由 Provider 的清理策略决定。保留的控制仍需当前授权。查询不会重置输出；原生进程完成不代表输出已完整捕获。可用动作和 provider 限制见 [Environment 工具](environments.md)。

## Host 检查点

持久 Host 应将检查点发布做成明确的应用操作。将选定状态与定义版本、来源、剩余的已接受输入及引用内容一起管理。允许新 Run 执行时，重新构造当前身份、凭据、策略和 Environment 绑定。

Host 另行负责 worker 尝试、租约、并发写入协调、持久完成和输出交付。Harness 终态结果确认 Run 的结果，Host 保存成功确认检查点已发布。完整边界见[嵌入 Host](hosting.md)，单应用实现见 [Agent Application 示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app)。
