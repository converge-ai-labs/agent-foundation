---
title: Live conversations
description: Run duplex text and audio conversations with Harness tools, state, and cleanup.
---

Use `ExecutableAgent.live()` when input and output overlap in one conversation. A Live Run uses the same Agent definition, tools, fresh Context, Environment bindings, plugins, and portable state as other Harness Runs. It opens a native realtime connection instead of the ordinary request/response loop.

Live is an embedded SDK API. It does not add a microphone UI, browser transport, durable socket recovery, or cross-Thread orchestration.

## Open a Live Run

Pass a native `RealtimeModel` instance explicitly. Provider credentials and connection options belong to that native Model. The ordinary Model configured for `run()` or `stream()` does not select the Live provider.

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

Set `OPENAI_API_KEY` and `OPENAI_REALTIME_MODEL` to credentials and a realtime model available to your account. This example prints transcripts, not sound. It needs provider access and can incur charges.

The repository also includes `dev/harness/live_smoke.py`. Run `uv run --locked python dev/harness/live_smoke.py --model YOUR_REALTIME_MODEL` from the checkout root. It checks an actual tool call, drains audio separately, requires one completed terminal result, and round-trips the exported state. Its 60-second timeout is a test deadline, not a Harness default.

## Send and play audio

Consume the single semantic iterator concurrently with microphone input and playback. `send_audio(bytes_or_async_iterable)` sends native PCM input. `stream_audio()` yields native PCM output. Use the selected native Model's audio profile for encoding and sample rate; Harness does not transcode device audio.

`commit_audio()`, `clear_audio()`, and `create_response()` expose native manual turn controls. Native profiles reject unsupported operations rather than silently ignoring them. Pass native realtime options through `model_settings`.

Call `interrupt(played_ms=...)` with the actual playback position to truncate and cancel speech where supported. Your player must discard its own buffered audio. Alternatively, keep exactly one `stream_audio()` subscriber and call `interrupt(played_bytes=...)` with cumulative PCM bytes actually played; native accounting handles unread audio and returns whether an interruption occurred. The two positions are mutually exclusive. `interrupt()` without a position cancels without truncation. Interruption does not undo a tool's effects or end the Harness Run.

Raw speech audio is removed from Harness semantic events and producer observations. `audio_retention="transcript_only"` is the default for history. Select native `input_audio`, `output_audio`, or `all` only when the Host intends to retain those bytes in exported messages.

## Tools, approvals, and context

Tools use the native tool manager and the existing Harness permission boundary. `CodeActCapability` exposes its prepared tool directory and supports the same explicit `store` and `load` values as ordinary Runs. Tool schemas are advertised when the connection opens; local policy and preparation can reject later calls but cannot advertise newly added schemas mid-session.

Use native `HandleDeferredToolCalls` for inline approval or external-result handling. A handler can await a Host decision while the conversation remains open. Rejected approval does not execute the tool. Run cancellation cancels a waiting handler. Without a handler, an unresolved call returns a native failed tool result; Live does not close into a durable `DeferredToolResume` handshake.

Initial model-context projection supplies connection instructions. Successful local tools and resolved external results append fresh model-context content before the native session sends the result and continues. Original tool values, evidence, and metadata are preserved. Native graph, model-request, and output-validation hooks are not conversational turn hooks.

## Completion and continuation

- `close()` ends the native session normally. Continue consuming until the final `HarnessRunResultEvent`; this is the post-cleanup receipt.
- `cancel()` requests a cancelled Run. It is distinct from speech interruption and normal closure.
- A conversational turn-complete event does not finish the Harness Run.
- Early async-context exit closes resources and retains a checkpoint, but does not create a terminal receipt.
- Provider, plugin, and cleanup errors propagate. No successful terminal receipt is emitted for failed teardown.

`export_state()` includes accepted native history, even when a sent text input has no reply yet. Pass the state as `previous_state` to open another Run on the same Thread. The Run ID and bindings are fresh. A resumed Live Run opens a new connection and seeds history only if its native Model supports it. Socket state, approval waiters, playback buffers, and temporary CodeAct bindings are not restored. Explicit stored values survive.

Live has no validated business output: `result.output` is `None`, regardless of the definition's ordinary `output_type`. Use retained messages for conversation content.

## Usage and limits

Live reports response contributions while active and settles them again after closure without double counting. Existing native usage limits and Harness attributed-budget checks apply. Caller-provided native usage baselines do not become new Run-local contributions.

`live.usage`, terminal usage, and `UsageSnapshot` explicitly carry `model_usage_coverage="responses_only"`. They cover observed model responses, plus independently attributed auxiliary work and provider receipts. They do not claim complete session billing: session-only transcription, duration, and other provider consumption may not be present in response history. Unknown costs remain unknown, and known partial cost is not a complete quote.

`live.native_usage` is a detached diagnostic copy of the native accumulator. It includes native session-only usage but may also include a caller baseline and nested work sharing that accumulator. Do not add it to `live.usage`, treat it as an attributed receipt, or assume it is restored from state. Hosts needing complete billing must reconcile against provider evidence.

Live rejects `RunBindings.model_call_check` before connecting with `live_model_check_unsupported`. Native realtime has no equivalent of the ordinary Host per-request reservation boundary. See [usage and limits](usage-and-limits.md) for ordinary accounting and [state and resume](state-and-resume.md) for Host persistence ownership.
