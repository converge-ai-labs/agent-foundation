---
title: 状态与恢复
description: 深入掌握 Agent 会话检查点持久化、JSON 状态序列化、会话分支 Fork，以及人机交互审批后的安全挂起与精准恢复机制。
---

生产级智能体架构必须能够从容应对进程重启、支持多轮复杂对话，并在涉及高风险工具或需人工确认时优雅挂起。**Harness** 将完整的历史上下文、工具执行结果快照以及各 Capability 状态封装在精简可序列化的 `HarnessState` 检查点中。

## 继续 Thread

在多次调用间维持会话连续性最简单的方式是传入 `previous_state`：

```python title="continue_thread.py"
import asyncio
import os

from a13n_harness import AgentSpec, HarnessBuilder


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="准确记忆多轮对话中的上下文细节。",
        ),
        output_type=str,
    )

    # 第一轮交互
    first = await executable.run("请记住这个密码代号：8492")
    print("第一轮：", first.output_or_raise())

    # 第二轮交互：传入上一轮的 first.state 续接上下文
    second = await executable.run(
        "我刚才让你记住的代码是什么？",
        previous_state=first.state,
    )
    print("第二轮：", second.output_or_raise())

    # 验证会话连续性
    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id


if __name__ == "__main__":
    asyncio.run(main())
```

当传入 `previous_state` 时，Harness 会自动沿用底层的 `thread_id`，将历史消息回填至模型上下文，并生成全新的 `run_id` 用于追踪本轮执行轨迹。

## 序列化状态

`HarnessState` 是标准的 Pydantic 模型，支持直接序列化为 JSON 字符串并存入 PostgreSQL、Redis 或 SQLite 等存储引擎：

```python
# 将状态序列化为 JSON 字符串
json_data = state.model_dump_json()

# 在其他微服务或后续进程中从 JSON 反序列化恢复
from a13n_harness import HarnessState
restored_state = HarnessState.model_validate_json(json_data)
```

产出的 JSON 数据格式高度紧凑，非常适合跨工作节点高效传输。

## 状态内容

`HarnessState` 完整封装了重建智能体对话所需的所有持久化数据：

| 字段 | 作用说明 |
| :--- | :--- |
| `thread_id` | 标识该对话连续流的唯一 UUID 字符串 |
| `messages` | 用户输入、模型回复与中间工具调用记录的有序消息序列 |
| `capabilities` | 各已启用 Capability 所保存的专属状态（如 CodeAct 变量、任务备忘录） |
| `metadata` | 宿主业务应用挂载的自定义元数据键值对 |

敏感认证信息（如 API 密钥、数据库连接句柄与网络 Socket）均保留在 `RunBindings` 中，绝不会被意外序列化进 `HarnessState`。

## Fork Thread

当需要基于当前对话探索不同分支、尝试不同决策路线而不污染原始历史时，可以使用 Fork 机制：

```python
# 派生独立分支，生成全新的 thread_id
forked_state = state.fork()

assert forked_state.thread_id != state.thread_id
assert len(forked_state.messages) == len(state.messages)

# 在派生分支上独立发起后续探索
forked_result = await executable.run(
    "探索另一个替代方案",
    previous_state=forked_state,
)
```

Fork 操作会深拷贝历史消息栈与能力状态，并赋予全新的 `thread_id`，原始分支的记录完全不受影响。

## 流式期间导出

在需要向前端流式推送 Token 的场景下，您也可以在执行全流程结束前增量捕获中间状态：

```python
async with executable.stream("生成一份深度的技术架构报告") as stream:
    async for item in stream:
        if isinstance(item, HarnessEvent):
            # 捕获流式输出过程中的特定事件
            pass
        elif isinstance(item, HarnessRunResultEvent):
            # 最终终态检查点
            final_state = item.result.state
```

最终派发的 `HarnessRunResultEvent` 提供了在底层所有工具连接与资源安全关闭后的权威状态快照。

### 压缩已保存的检查点

对于长周期运行、接近模型上下文极限的会话，Harness 提供了上下文压缩（Compaction）机制，能够在保留核心系统约束的前提下提炼早期历史轮次并清理冗余工具结果。

## 结构化挂起

当 Agent 在调用敏感工具前需要人工授权，或需要等待外部系统异步回填数据时，Harness 会优雅地将执行状态转入挂起（Suspension）：

```python
result = await executable.run("删除生产环境数据库集群")

if result.status == "suspended":
    # 1. 持久化保存挂起检查点
    save_checkpoint(result.state)

    # 2. 提取等待审批的工具调用或交互式提问
    for deferred in result.deferred:
        print(f"待审批工具：{deferred.tool_name}，入参：{deferred.arguments}")
```

此时 Run 的状态将变为 `suspended`，所有未完成的调用信息完整记录在 `result.deferred` 中供外部审批系统消费。

## 恢复中断的延迟续接

当管理员在界面上完成审批，或异步回调结果就绪后，通过 `DeferredToolResume` 恢复执行：

```python
from a13n_harness import DeferredToolResume

resume_payload = DeferredToolResume(
    tool_call_id="call_abc123",
    content="管理员已批准此项操作",
)

resumed_result = await executable.run(
    resume_payload,
    previous_state=result.state,
)
```

Harness 会精准重返挂起点，将外部审批结果直接交付给等待中的模型继续执行。

## 恢复未回答工具调用

当外部依赖服务在执行中途发生网络抖动或崩溃时，Harness 允许宿主平台注入显式的容错恢复策略。

### 在来源声明可重试工具

在定义函数工具时，将幂等的只读查询操作声明为可安全重试：

```python
def fetch_weather(city: str) -> dict:
    """获取指定城市的天气数据（只读幂等查询）。"""
    return {"city": city, "temp_c": 22}

# 标记为幂等重试工具
fetch_weather.__retryable__ = True
```

### 按策略恢复

在未完成调用的中断场景下恢复会话时，显式指定恢复策略：

```python
from a13n_harness import ResumePolicy

resumed = await executable.run(
    "重试未决操作",
    previous_state=interrupted_state,
    resume_policy=ResumePolicy.RETRY_IDEMPOTENT,
)
```

系统会自动重试被标记为幂等的工具，而对于非幂等写操作则主动提示模型进行二次确认。

## 安全的失败状态候选

当执行因模型速率超限（Rate Limit）或外部网络断开而失败时，`result.state` 会捕获一个安全的候选快照：

```python
try:
    result = await executable.run("处理关键业务数据")
except Exception:
    # Harness 在结果对象中保留了部分安全状态候选
    pass
```

您可以通过 `result.failure` 准确审计故障根因，并自主选择是回滚到上一次确定性状态还是针对当前步发起重试。

## Capability 状态

各个 Capabilities 在 `HarnessState` 中拥有独立的命名空间隔离：

```python
# 各能力维护自身的专属状态隔离域
state.capabilities.get("working_state")
state.capabilities.get("codeact_runtime")
```

这种模块化设计确保了第三方插件与自定义能力绝不会污染核心消息结构或发生数据冲突。

## Shell 执行与观测

当配合沙箱环境使用时，Harness 会在能力状态中追踪后台子进程 PID 与执行句柄。即便单次调用结束，后台持续编译的进程状态仍会被准确追踪，方便在后续轮次中持续监测输出或主动终止。

## Host 检查点

在生产高并发服务中，建议遵循标准宿主检查点落盘设计模式：

1. 在确认用户消息提交的同一数据库事务中，原子写入 `result.state`。
2. 按照 `thread_id` 与时间戳建立高效索引。
3. 节点重启后，根据会话标识直接加载最新的 `HarnessState` 检查点平滑接续。
