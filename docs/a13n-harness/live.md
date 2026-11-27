---
title: Live conversations
description: Run real-time bidirectional audio and text conversations in Harness with live tool execution, approvals, and portable state continuation.
---

A Live Run keeps a bidirectional session open while the user speaks, the Model responds, and tools execute. Your application owns device audio capture and playback; Harness supplies the Agent's tools, permissions, Context, plugins, and portable state.

| Integration                                           | API                        | Application responsibility                      |
| ----------------------------------------------------- | -------------------------- | ----------------------------------------------- |
| Let a native realtime Model call Harness tools        | `ExecutableAgent.live()`   | Provide the Model, audio/text input, and player |
| Connect subscription voice to Host-managed Agent work | `CodexLiveModel.connect()` | Provide a WebRTC peer and admit delegated tasks |

For an existing browser microphone UI, see [Harness UI Live mode](../a13n-harness-ui/live-mode.md).

## Open a Live Run

Start with one text exchange to verify connection lifecycle and state export before wiring audio devices. Set `OPENAI_API_KEY` and `OPENAI_REALTIME_MODEL`, save `live_demo.py`, and run `uv run --locked python live_demo.py`:

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

Pass a native `RealtimeModel` explicitly to `live()`; each Live Run binds fresh Context and Environment handles. You can also verify tool calls and audio draining with `uv run --locked python dev/harness/live_smoke.py --model YOUR_REALTIME_MODEL`.

## Send audio and handle interruptions

Run three concurrent tasks for full-duplex voice:

1. Feed PCM chunks matching the Model's audio profile to `live.send_audio(bytes_or_async_iterable)`.
2. Iterate `async for item in live` for transcripts, tool events, and the terminal `HarnessRunResultEvent`.
3. Drain `live.stream_audio()` into your audio player (raw speech bytes are stripped from semantic events; `audio_retention` defaults to `"transcript_only"` and also supports `"input_audio"`, `"output_audio"`, or `"all"` for exported history).

For manual turn boundaries where supported, call `commit_audio()`, `clear_audio()`, and `create_response()`. When the user barges in, stop local playback and call one interruption variant:

| Call                          | Meaning                                                                             |
| ----------------------------- | ----------------------------------------------------------------------------------- |
| `interrupt(played_ms=...)`    | Truncate at actual playback position in milliseconds                                |
| `interrupt(played_bytes=...)` | Truncate by cumulative played PCM bytes (requires a single `stream_audio` consumer) |
| `interrupt()`                 | Cancel current generation without truncating at a playback position                 |

## Tools, inline approvals, continuation, and usage

Native tool schemas are advertised when the connection opens. To approve sensitive calls or supply external tool results while keeping the voice socket open, register `HandleDeferredToolCalls` in the Agent's capabilities or `RunBindings` so deferred calls resolve inline on the active connection. Harness projects initial Context into session instructions and appends refreshed Context after tool calls complete.

On Models that support live tool updates, `load_capability` adds deferred tools mid-session and its result still carries refreshed Context. Models with a fixed tool set reject deferred capabilities that contribute tools and have not yet been loaded when the connection opens.

Call `close()` for normal completion (or `cancel()` to abort), finish iterating through `HarnessRunResultEvent` (`result.output` is always `None` because speech lives in messages), and persist `await live.export_state()`. Pass `previous_state=restored` to `agent.live(...)` to continue the Thread over a new connection.

Read `live.usage` or terminal `UsageSnapshot` for attributed accounting (`model_usage_coverage="responses_only"`, excluding session-only transcription or duration fees; inspect `live.native_usage` only for raw diagnostics). Note that `RunBindings.model_call_check` is rejected in Live mode (`live_model_check_unsupported`).

## Connect Codex voice

Use `CodexLiveModel` (`a13n_harness.models.codex_live`) when subscription voice delegates tasks to your Host's existing Agent workflow:

1. Construct `CodexLiveModel` with an `OpenAICodexCredentialSource` ([Model authentication](model-authentication.md)) and an optional `CodexLiveVoice` (default `"cove"`).
2. Create a WebRTC offer in your media peer and enter `async with model.connect(offer_sdp, instructions=..., on_answer=..., initial_items=...) as session:`. Apply `answer_sdp` in `on_answer` and wait for peer connectivity before returning.
3. Consume `session.events()` and route `SessionReady`, `Transcript(role, text, final)`, and `Delegation(item_id, text, context)`.
4. Push user-facing results back with `await session.append_speech(text, delegation_id=...)` and progress updates with `await session.append_status(...)` (never send private reasoning).
