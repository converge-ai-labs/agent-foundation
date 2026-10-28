---
title: Live 实时对话
description: 打开实时对话，处理工具和音频，再关闭连接并通过可移植状态续接。
---

Live Run 在用户说话、Model 回复和工具执行期间保持对话连接。应用管理输入和播放，Harness 提供 Agent 的工具、权限、Context、插件和状态。

根据谁负责运行 Agent 工作来选择集成方式：

| 任务                                      | API                        | 应用职责                                |
| ----------------------------------------- | -------------------------- | --------------------------------------- |
| 让原生实时 Model 调用 Harness 工具        | `ExecutableAgent.live()`   | 提供 Model、输入和播放                  |
| 将订阅语音连接到 Host 管理的 Agent 工作流 | `CodexLiveModel.connect()` | 提供 WebRTC peer 并决定是否接受委派任务 |

下文首先介绍原生 Live Run。[Codex 语音](#connect-codex-voice)使用独立传输 API。需要现成的麦克风界面时，可使用 [Harness UI Live 模式](../a13n-harness-ui/live-mode.md)。

## 打开 Live Run

先完成一次文本交互，验证连接和清理，再接入音频设备。

1. 按[快速开始](getting-started.md)准备 Python 环境。
2. 设置 `OPENAI_API_KEY`，并将 `OPENAI_REALTIME_MODEL` 设为该账号可用的实时模型。示例会发出 provider 请求，可能产生费用。
3. 将以下代码保存为 `live_demo.py`，在仓库根目录运行 `uv run --locked python live_demo.py`。

```python
import asyncio
import os

from a13n_harness import (
    AgentDefinition, AgentSpec, HarnessBuilder, HarnessEvent,
    HarnessRunResultEvent, HarnessState,
)
from pydantic_ai.messages import PartEndEvent, RealtimeTurnCompleteEvent, SpeechPart
from pydantic_ai.realtime.openai import OpenAIRealtimeModel


async def exchange(agent, model, prompt, previous_state=None):
    terminals = 0
    async with asyncio.timeout(60), agent.live(
        model=model, previous_state=previous_state,
    ) as live:
        await live.send(prompt, respond=True)
        async for item in live:
            if isinstance(item, HarnessEvent):
                event = item.event
                if isinstance(event, PartEndEvent) and isinstance(event.part, SpeechPart):
                    print(event.part.transcript or "")
                if isinstance(event, RealtimeTurnCompleteEvent):
                    await live.close()
            elif isinstance(item, HarnessRunResultEvent):
                terminals += 1
                assert item.result.status == "completed", item.result.failure
                assert item.result.output is None
        assert terminals == 1
        return await live.export_state()


async def main():
    agent = HarnessBuilder().build(
        AgentDefinition(output_type=str, agent=AgentSpec(instructions="Answer briefly."))
    )
    model = OpenAIRealtimeModel(os.environ["OPENAI_REALTIME_MODEL"])
    state = await exchange(agent, model, "Say hello.")
    saved_json = state.model_dump_json()
    restored = HarnessState.model_validate_json(saved_json)
    assert restored.thread_id == state.thread_id
    print(restored.thread_id)


if __name__ == "__main__":
    asyncio.run(main())
```

程序打印转写文本和 Thread ID。它在第一个对话轮次完成后关闭连接，消费最终 Run 结果，并验证导出状态可以序列化后恢复。60 秒期限由示例设置。程序显示语音文字，不播放音频。

调用 `live()` 时显式传入原生 `RealtimeModel`。它的凭据和连接设置独立于 `run()` 或 `stream()` 使用的普通 Model。每个 Live Run 获得新的 Context 和 Environment 绑定，并使用同一 Agent 定义。

仓库中的 `dev/harness/live_smoke.py` 提供更完整的 provider 检查。在仓库根目录准备好同一 API key 后运行：

```bash
uv run --locked python dev/harness/live_smoke.py --model YOUR_REALTIME_MODEL
```

将模型占位符替换为账号支持的实时模型。脚本请求一次实际工具调用，独立消费音频，检查唯一的完成终态结果，并验证状态的序列化往返。

## 发送和播放音频

麦克风应用需要同时进行三项工作：

1. 用 `send_audio(bytes_or_async_iterable)` 发送原生 PCM 输入。
2. 消费唯一的语义迭代器，处理转写、工具事件和最终 Run 结果。
3. 消费 `stream_audio()`，将 PCM 数据块交给播放器。

编码和采样率遵循所选原生 Model 的音频 profile。应用负责转换设备音频，Harness 转发原生格式。通过 `model_settings` 传入实时选项。需要手动控制轮次时，在原生 profile 支持的情况下使用 `commit_audio()`、`clear_audio()` 和 `create_response()`；不支持的操作会报错。

用户打断时，先停止本地播放并丢弃缓冲音频，再选择一种播放位置约定：

| 调用                          | 播放器提供的位置                                                    |
| ----------------------------- | ------------------------------------------------------------------- |
| `interrupt(played_ms=...)`    | 以毫秒计的实际播放位置                                              |
| `interrupt(played_bytes=...)` | 实际已播放的累计 PCM 字节数，且始终只有一个 `stream_audio()` 订阅者 |
| `interrupt()`                 | 取消，但不按播放位置截断                                            |

毫秒和字节参数互斥。原生字节计量负责未读取音频的处理，并报告是否发生打断。打断会保持 Run 开启，也不会撤销已完成的工具副作用。

只保留转写历史时，使用默认的 `audio_retention="transcript_only"`。Host 需要在导出消息中保留音频字节时，再选择 `input_audio`、`output_audio` 或 `all`。Harness 语义事件和 producer observation 会移除原始语音音频；播放使用独立音频流。

## 工具、审批与上下文

在打开连接前配置工具和权限。原生工具 schema 在连接时公布。本地准备逻辑和策略仍会检查后续调用，但连接中途新增的 schema 不会重新公布。`CodeActCapability` 使用已准备的工具目录，并支持与普通 Run 相同的显式 `store`、`load` 值。

需要审批或外部结果时，在 Agent capabilities 或当前 Run bindings 中安装原生 `HandleDeferredToolCalls`。其 handler 可以等待 Host 决策，同时保持语音连接。拒绝审批会阻止工具执行；取消 Run 会取消等待中的 handler。没有 handler 时，未解决的调用成为原生工具失败结果。决策界面应围绕这种内联等待构建，而不是使用普通 Run 的持久化 `DeferredToolResume` 流程。

Harness 将初始模型上下文投影为连接指令。成功执行本地工具或取得外部结果后，它会在原生 session 发送结果并继续前附加更新后的上下文，同时保留原始值、证据和元数据。用实时事件跟踪对话轮次；graph、model-request 和 output-validation hook 不承担轮次 hook 的职责。

## 完成与续接

先关闭 session，再消费完迭代器，最后持久化状态。各操作的含义不同：

| 操作或事件                   | 含义                                      |
| ---------------------------- | ----------------------------------------- |
| 对话轮次完成                 | session 可以接受下一轮输入                |
| `close()`                    | 正常结束原生 session                      |
| `cancel()`                   | 请求取消 Run                              |
| 最终 `HarnessRunResultEvent` | 取得清理完成后的结果                      |
| 提前退出异步上下文           | 关闭资源并保留 checkpoint，但没有终态回执 |

Provider、插件和清理错误会传播给应用。清理失败不会产生成功终态回执。Live 对话内容保存在消息中：即使 Agent 的普通 `output_type` 是 `str`，`result.output` 仍为 `None`。

通过保存的 JSON 和前面的 `exchange` helper 续接。将以下 helper 加入同一模块，再从应用的异步入口传入 Agent、原生 Model 和已保存的状态：

```python
async def continue_live(agent, model, saved_json):
    previous = HarnessState.model_validate_json(saved_json)
    current = await exchange(
        agent, model, "Continue our conversation.", previous_state=previous,
    )
    assert current.thread_id == previous.thread_id
    return current.model_dump_json()
```

Host 通过自己的持久化机制保存返回的 JSON。`export_state()` 包含原生 session 已接受的历史，也包括已发送但尚未得到回复的文本。续接会打开新连接，使用新的 Run ID 和绑定。注入历史需要原生 Model 支持。显式存储的值会保留；socket、审批等待器、播放器缓冲区和临时 CodeAct 变量属于旧连接或旧 Run。

## 用量与限制

通过 `live.usage`、终态用量或 `UsageSnapshot` 获取已归属的 Run 用量。Live 在执行中更新响应贡献，关闭后再结算，避免重复计数。原生 usage limit 和 Harness 已归属用量预算检查均生效。调用方传入的原生用量基线不计入新 Run 的本地贡献。

覆盖标记为 `model_usage_coverage="responses_only"`：包括已观察到的模型响应，以及独立归属的辅助工作和 provider 回执。仅属于 session 的转写、时长或其他 provider 消耗可能缺失。完整账单需要与 provider 证据对账；未知成本保持未知，已知部分成本不等于完整 session 报价。

`live.native_usage` 用于诊断原生累计器。这个独立副本可能同时包含 session-only 用量、调用方基线和共享累计器的嵌套工作。将它加到已归属用量会混合不同计量范围。它不是已归属回执，也不会从可移植状态中恢复。

Live 在连接前以 `live_model_check_unsupported` 拒绝 `RunBindings.model_call_check`：原生实时执行没有与普通 Host 逐请求预算预留等价的边界。普通计量参阅[用量与限制](usage-and-limits.md)，持久化参阅[状态与恢复](state-and-resume.md)。

## 连接 Codex 语音

语音连接需要将工作委派给 Host 现有的 Agent 执行流程时，使用 `CodexLiveModel`。工厂创建语音通话和控制连接；应用负责 WebRTC 媒体 peer、任务准入、Agent Run、持久化和展示。

1. 从 `a13n_harness.models.codex_live` 导入 `CodexLiveModel`，使用[认证集成](model-authentication.md)提供的 `OpenAICodexCredentialSource` 构造实例。默认音色为 `cove`，其他值必须是受支持的 `CodexLiveVoice` V3 名称。
2. 在媒体 peer 中创建 WebRTC offer。以异步上下文管理器进入 `model.connect(offer_sdp, instructions=..., on_answer=..., initial_items=...)`。可通过 `ConversationItem` 序列传入先前上下文。
3. 在异步 `on_answer(answer_sdp)` 回调中应用 answer，并等待 peer 连接。回调必须结束后才能接入控制连接。订阅凭据留在 Host，浏览器接收 SDP answer。
4. 消费 `session.events()`，按下表将事件交给应用处理。
5. 退出上下文管理器，关闭语音连接，并在 Host 清理阶段停止媒体 peer。

| 事件                                 | 应用操作                                                    |
| ------------------------------------ | ----------------------------------------------------------- |
| `SessionReady`                       | 展示 provider 就绪状态                                      |
| `Transcript(role, text, final)`      | 显示语音文本，区分临时和最终文本                            |
| `Delegation(item_id, text, context)` | 决定是否接受任务，再将其文本和有界上下文提交给 Agent 工作流 |

通过 `session.append_speech(text, delegation_id=...)` 返回选定的用户可见结果，通过 `session.append_status(...)` 返回进度。可选 ID 将文本与委派关联。这些写入为语音提供上下文；任务完成、实际播放和持久送达需要各自的应用证据。只发送用户可见文本，两个通道都不包含私有推理。

每次连接最多创建一个通话。创建通话或写入上下文的结果不确定时，处理错误并让用户重新开始语音会话；工厂不会重放这些操作。连接仍需要账号具备使用资格，且 provider 可用。
