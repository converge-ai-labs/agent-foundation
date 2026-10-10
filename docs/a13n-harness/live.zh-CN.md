---
title: Live 实时对话
description: 在双向文本和音频对话中使用 Harness 工具、状态与清理机制。
---

输入和输出需要在同一对话中并行时，使用 `ExecutableAgent.live()`。Live Run 复用其他 Harness Run 的 Agent 定义、工具、新 Context、Environment 绑定、插件和可移植状态，但打开原生实时连接，而不是运行普通请求/响应循环。

Live 是嵌入式 SDK API，不包含麦克风 UI、浏览器传输、持久化连接恢复或跨 Thread 编排。

## 打开 Live Run

显式传入原生 `RealtimeModel` 实例。Provider 凭据与连接选项由该原生 Model 管理。为 `run()` 或 `stream()` 配置的普通 Model 不会决定 Live provider。

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
