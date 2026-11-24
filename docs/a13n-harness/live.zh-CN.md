---
title: 实时语音与多模态对话
description: 在 Harness 中运行实时双向语音与文本对话，支持在线工具调用、内联审批与可移植状态续接。
---

Live 运行在用户说话、模型响应与工具执行期间保持双向连接打开。你的应用负责设备音频采集与播放；Harness 提供智能体的工具、权限、上下文、插件与可移植状态。

| 集成场景                                   | API                        | 应用职责                            |
| ------------------------------------------ | -------------------------- | ----------------------------------- |
| 让原生实时模型直接调用 Harness 工具        | `ExecutableAgent.live()`   | 提供实时模型、音视频/文本输入与播放 |
| 将订阅语音连接到宿主托管的智能体执行工作流 | `CodexLiveModel.connect()` | 维护 WebRTC 对端并准入委派任务      |

如需现成的浏览器麦克风界面，请参阅 [Harness UI 实时语音模式](../a13n-harness-ui/live-mode.md)。

## 开启 Live 运行

建议先用一次纯文本交互验证连接生命周期与状态导出，再接入音频设备。配置 `OPENAI_API_KEY` 与 `OPENAI_REALTIME_MODEL`，保存 `live_demo.py` 并运行 `uv run --locked python live_demo.py`：

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
                assert item.result.status == "completed", item.result.failure
                assert item.result.output is None
        return await live.export_state()


async def main():
    agent = HarnessBuilder().build(
        AgentDefinition(output_type=str, agent=AgentSpec(instructions="Answer briefly."))
    )
    model = OpenAIRealtimeModel(os.environ["OPENAI_REALTIME_MODEL"])
    state = await exchange(agent, model, "Say hello.")
    restored = HarnessState.model_validate_json(state.model_dump_json())
    print(restored.thread_id)


if __name__ == "__main__":
    asyncio.run(main())
```

向 `live()` 显式传入原生 `RealtimeModel`；每次 Live 运行都会绑定全新的上下文与环境句柄。你也可以运行 `uv run --locked python dev/harness/live_smoke.py --model YOUR_REALTIME_MODEL` 验证工具调用与音频排空。

## 发送音频与处理打断

全双工语音应用通常并发执行三项任务：

1. 将符合模型音频采样率与编码规格的 PCM 数据块送入 `live.send_audio(bytes_or_async_iterable)`。
2. 迭代 `async for item in live` 接收转写文本、工具事件与终态 `HarnessRunResultEvent`。
3. 消费 `live.stream_audio()` 将 PCM 数据块送往播放器（语义事件中已剥离原始音频字节；导出消息的 `audio_retention` 默认为 `"transcript_only"`，也可选 `"input_audio"`、`"output_audio"` 或 `"all"`）。

模型支持手动轮次控制时，可调用 `commit_audio()`、`clear_audio()` 与 `create_response()`。当用户开口打断时，先停止本地播放，再调用以下任一打断方式：

| 调用                          | 含义                                                        |
| ----------------------------- | ----------------------------------------------------------- |
| `interrupt(played_ms=...)`    | 按实际已播放毫秒位置截断                                    |
| `interrupt(played_bytes=...)` | 按累计已播放 PCM 字节数截断（要求单一 `stream_audio` 订阅） |
| `interrupt()`                 | 取消当前生成，不按播放位置截断                              |

## 工具、内联审批、状态续接与用量

原生工具 Schema 会在建立连接时一次性宣告。若需在保持语音连接不断开的前提下审批敏感调用或回填外部工具结果，请在能力列表或 `RunBindings` 中注册 `HandleDeferredToolCalls`，在当前长连接内直接完成延迟调用。Harness 会将初始上下文注入会话指令，并在工具调用完成后追加刷新后的上下文。

正常结束调用 `close()`（中止调用 `cancel()`），排空迭代器收到 `HarnessRunResultEvent`（由于对话内容保存在消息历史中，`result.output` 恒为 `None`），然后持久化 `await live.export_state()`。下次调用 `agent.live(..., previous_state=restored)` 即可在新连接上续接同一会话。

读取 `live.usage` 或终态 `UsageSnapshot` 获取归因用量（`model_usage_coverage="responses_only"`，不含纯会话级转写或时长计费；`live.native_usage` 仅供底层诊断）。注意 Live 模式不支持逐请求预留检查，传入 `RunBindings.model_call_check` 会以 `live_model_check_unsupported` 拒绝连接。

## 接入 Codex 语音

当订阅制语音需要将任务委派给宿主现有的智能体工作流时，请使用 `CodexLiveModel`（`a13n_harness.models.codex_live`）：

1. 使用 `OpenAICodexCredentialSource`（参见[模型认证](model-authentication.md)）和可选 `CodexLiveVoice`（默认 `"cove"`）构造 `CodexLiveModel`。
2. 在媒体对端生成 WebRTC Offer，进入 `async with model.connect(offer_sdp, instructions=..., on_answer=..., initial_items=...) as session:`，并在 `on_answer` 回调中应用 `answer_sdp` 且等待对端连通后再返回。
3. 消费 `session.events()`，分发处理 `SessionReady`、`Transcript(role, text, final)` 与 `Delegation(item_id, text, context)`。
4. 通过 `await session.append_speech(text, delegation_id=...)` 回传面向用户的结果播报，通过 `await session.append_status(...)` 同步进度状态（切勿写入内部推理内容）。
