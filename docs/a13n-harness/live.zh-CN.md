---
title: Live 实时对话
description: 在双向文本和音频对话中使用 Harness 工具、状态与清理机制。
---

根据要构建的对话选择 API：

| 任务                                          | API                        | 应用提供的部分                           |
| --------------------------------------------- | -------------------------- | ---------------------------------------- |
| 运行带原生实时工具和可移植状态的 Agent        | `ExecutableAgent.live()`   | 原生 `RealtimeModel`、输入和音频播放     |
| 将订阅认证的语音接入 Host 管理的 Agent 工作流 | `CodexLiveModel.connect()` | WebRTC 媒体 peer、凭据来源和委派处理逻辑 |

需要现成的麦克风界面时，参阅 [Harness UI Live 模式](../a13n-harness-ui/live-mode.md)。下文先介绍原生 Live Run；[Codex 语音集成](#connect-codex-voice)使用独立的传输 API。

## 打开 Live Run

Live Run 使用 Agent 的工具、新 Context、Environment 绑定、插件和可移植状态。显式传入原生 `RealtimeModel` 来打开实时连接。该 Model 管理 provider 凭据和连接选项；为 `run()` 或 `stream()` 配置的普通 Model 保持独立。

```python
import asyncio
import os

from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, HarnessEvent, HarnessRunResultEvent
from pydantic_ai.messages import PartEndEvent, RealtimeTurnCompleteEvent, SpeechPart
from pydantic_ai.realtime.openai import OpenAIRealtimeModel


async def main():
    agent = HarnessBuilder().build(
        AgentDefinition(output_type=str, agent=AgentSpec(instructions="Answer briefly."))
    )
    model = OpenAIRealtimeModel(os.environ["OPENAI_REALTIME_MODEL"])
    async with asyncio.timeout(60), agent.live(model=model) as live:
        await live.send("Say hello.", respond=True)
        async for item in live:
            if isinstance(item, HarnessEvent):
                event = item.event
                if isinstance(event, PartEndEvent) and isinstance(event.part, SpeechPart):
                    print(event.part.transcript or "")
                if isinstance(event, RealtimeTurnCompleteEvent):
                    await live.close()
            elif isinstance(item, HarnessRunResultEvent):
                assert item.result.status == "completed"
                assert item.result.output is None
        state = await live.export_state()
        print(state.thread_id)


asyncio.run(main())
```

将 `OPENAI_API_KEY` 和 `OPENAI_REALTIME_MODEL` 设置为有效凭据及账号可用的实时模型。此示例只打印转写文本，不播放声音。运行需要 provider 访问权限，并可能产生费用。

仓库还提供 `dev/harness/live_smoke.py`。在仓库根目录运行 `uv run --locked python dev/harness/live_smoke.py --model YOUR_REALTIME_MODEL`。该脚本验证实际工具调用、独立消费音频、唯一的完成结果，以及导出状态的序列化往返。60 秒超时属于测试期限，不是 Harness 默认限制。

## 发送和播放音频

在发送麦克风输入和播放音频的同时，消费唯一的语义事件迭代器。`send_audio(bytes_or_async_iterable)` 发送原生 PCM 输入，`stream_audio()` 产生原生 PCM 输出。编码与采样率遵循所选原生 Model 的音频 profile；Harness 不负责设备音频转码。

`commit_audio()`、`clear_audio()` 和 `create_response()` 暴露原生手动轮次控制。不支持的操作由原生 profile 明确拒绝，不会静默忽略。通过 `model_settings` 传入原生实时选项。

在支持截断的模型上，调用 `interrupt(played_ms=...)`，传入实际播放位置，截断并取消语音。播放器必须自行丢弃本地缓冲音频。也可以始终保持恰好一个 `stream_audio()` 订阅者，并调用 `interrupt(played_bytes=...)`，传入实际已播放的累计 PCM 字节数；原生实现负责未读取音频的处理，并返回是否发生打断。两种播放位置互斥。不传位置的 `interrupt()` 只取消，不截断。打断不会撤销工具副作用，也不会结束 Harness Run。

Harness 语义事件和 producer observation 中不包含原始语音音频。历史默认使用 `audio_retention="transcript_only"`。只有 Host 明确希望在导出消息中保留音频字节时，才选择原生 `input_audio`、`output_audio` 或 `all`。

## 工具、审批与上下文

工具通过原生 tool manager 和现有 Harness 权限边界执行。`CodeActCapability` 暴露已准备的工具目录，并支持与普通 Run 相同的显式 `store`、`load` 值。工具 schema 在连接打开时公布；本地策略和准备逻辑仍可拒绝后续调用，但不能在连接中途公布新增 schema。

使用原生 `HandleDeferredToolCalls` 处理内联审批或外部结果。Handler 可以等待 Host 决策，同时保持对话连接。拒绝审批不会执行工具。Run 取消会取消等待中的 handler。没有 handler 时，未解决的调用返回原生工具失败结果；Live 不会通过关闭连接进入持久化 `DeferredToolResume` 握手。

初始 model-context 投影提供连接指令。成功执行的本地工具及已解决的外部结果会在原生 session 发送结果并继续对话前附加新的 model-context 内容。原工具值、证据和元数据会保留。原生 graph、model-request 和 output-validation hook 不等同于实时对话轮次 hook。

## 完成与续接

- `close()` 正常结束原生 session。继续消费到最终 `HarnessRunResultEvent`，才能取得清理完成后的回执。
- `cancel()` 请求取消 Run，与语音打断和正常关闭不同。
- 对话轮次完成事件不会结束 Harness Run。
- 提前退出异步上下文会关闭资源并保留 checkpoint，但不会生成终态回执。
- Provider、插件和清理错误会向调用方传播。清理失败不会产生成功终态回执。

`export_state()` 包含已接受的原生历史，包括尚未收到回复的已发送文本。将状态作为 `previous_state` 传入，即可在同一 Thread 上打开新 Run。Run ID 与绑定均为新的。恢复的 Live Run 会打开新连接，且仅在原生 Model 支持时注入历史。连接状态、审批等待器、播放缓冲区和临时 CodeAct 变量不会恢复；显式存储的值会保留。

Live 不产生经过校验的业务输出：无论定义中的普通 `output_type` 是什么，`result.output` 都是 `None`。对话内容通过保留的消息读取。

## 用量与限制

Live 在运行中报告响应贡献，并在关闭后再次结算，不会重复计数。现有原生 usage limit 和 Harness 已归属用量预算检查仍然生效。调用方传入的原生用量基线不会成为新 Run 的本地贡献。

`live.usage`、终态用量和 `UsageSnapshot` 显式携带 `model_usage_coverage="responses_only"`。其覆盖已观察到的模型响应，以及独立归属的辅助工作和 provider 回执；不声称覆盖完整 session 账单。仅属于 session 的转写、时长及其他 provider 消耗可能不出现在响应历史中。未知成本仍然未知，已知部分成本也不等于完整报价。

`live.native_usage` 是原生累计器的独立诊断副本。它包含原生 session-only 用量，但也可能包含调用方基线和共享累计器的嵌套工作。不要将其加到 `live.usage`，不要视为已归属回执，也不要假定它会从状态中恢复。需要完整计费的 Host 必须使用 provider 证据进行对账。

Live 会在连接前以 `live_model_check_unsupported` 拒绝 `RunBindings.model_call_check`。原生实时执行没有与普通 Host 逐请求预算预留等价的边界。普通计量规则见[用量与限制](usage-and-limits.md)，Host 持久化职责见[状态与恢复](state-and-resume.md)。

## 连接 Codex 语音

从 `a13n_harness.models.codex_live` 导入 `CodexLiveModel`，将订阅语音接入应用的 Agent 工作流。工厂负责创建通话和控制连接；Host 负责 WebRTC 媒体 peer、Agent 执行、持久化和展示。

1. 使用 `OpenAICodexCredentialSource` 创建 `CodexLiveModel(credential_source)`。默认音色为 `cove`；可选择其他 `CodexLiveVoice` V3 值。
2. 在媒体 peer 中创建 WebRTC offer。以异步上下文管理器进入 `model.connect(offer_sdp, instructions=..., on_answer=..., initial_items=...)`。`initial_items` 是可选的 `ConversationItem` 序列。
3. 在异步 `on_answer(answer_sdp)` 回调中，将 answer 应用到 peer 并等待连接建立。回调完成后才会接入控制连接。订阅凭据留在 Host，浏览器只接收 SDP answer。
4. 消费 `session.events()`，处理下表中的类型化事件。应用决定接受哪些委派任务，并通过正常的 Agent 执行路径启动它们。
5. 退出上下文管理器以关闭语音连接，并在 Host 清理时停止媒体 peer。

| 事件                                 | 应用操作                               |
| ------------------------------------ | -------------------------------------- |
| `SessionReady`                       | 展示 provider 就绪状态                 |
| `Transcript(role, text, final)`      | 显示语音文本，区分临时和最终文本       |
| `Delegation(item_id, text, context)` | 将任务及有界对话上下文提交给执行工作流 |

通过 `session.append_speech(text, delegation_id=...)` 发送选定的用户可见结果，通过 `session.append_status(...)` 发送进度。可选 ID 将文本与委派关联。发送文本是为语音提供上下文，不代表已经播放或持久送达。两个通道都不应发送私有推理。

每次连接最多创建一个通话。创建通话或写入上下文的结果不确定时，不会自动重放；应用应处理错误，让用户重新开始语音会话。连接时需要账号具备使用资格，且 provider 可用。
