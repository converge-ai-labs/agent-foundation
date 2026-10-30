---
title: Live conversations
description: Open a realtime conversation, handle tools and audio, then close and continue it with portable state.
---

A Live Run keeps a conversation open while the user speaks, the Model responds, and tools execute. Your application manages input and playback; Harness supplies the Agent's tools, permissions, Context, plugins, and state.

Choose the integration by who runs the Agent work:

| Task                                                  | API                        | Application responsibility                      |
| ----------------------------------------------------- | -------------------------- | ----------------------------------------------- |
| Let a native realtime Model call Harness tools        | `ExecutableAgent.live()`   | Provide the Model, input, and playback          |
| Connect subscription voice to Host-managed Agent work | `CodexLiveModel.connect()` | Provide a WebRTC peer and admit delegated tasks |

The first workflow below uses a native Live Run. [Codex voice](#connect-codex-voice) has a separate transport API. For an existing microphone interface, use [Harness UI Live mode](../a13n-harness-ui/live-mode.md).

## Open a Live Run

Start with one text exchange so you can verify the connection and cleanup before adding audio devices.

1. Prepare the Python environment from [Quickstart](getting-started.md).
2. Set `OPENAI_API_KEY` and set `OPENAI_REALTIME_MODEL` to a realtime model available to that account. This example makes a paid provider request when applicable.
3. Save the following as `live_demo.py` and run `uv run --locked python live_demo.py` from the checkout root.

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

The program prints a transcript and a Thread ID. It closes after the first completed conversational turn, consumes the final Run result, and round-trips the exported state. The 60-second deadline belongs to this example. It prints speech text without playing audio.

Pass a native `RealtimeModel` explicitly to `live()`. Its credentials and connection settings are independent of the ordinary Model used by `run()` or `stream()`. Each Live Run gets fresh Context and Environment bindings while using the same Agent definition.

For a broader provider check, the repository includes `dev/harness/live_smoke.py`. From the checkout root, with the same API key available, run:

```bash
uv run --locked python dev/harness/live_smoke.py --model YOUR_REALTIME_MODEL
```

That script requests an actual tool call, drains audio separately, checks one completed terminal result, and round-trips state. Replace the model placeholder with an account-supported realtime model.

## Send and play audio

A microphone application needs three concurrent activities:

1. Feed native PCM input to `send_audio(bytes_or_async_iterable)`.
2. Consume the single semantic iterator for transcripts, tool events, and the final Run result.
3. Consume `stream_audio()` and send its PCM chunks to your player.

Use the selected native Model's audio profile for encoding and sample rate. Convert device audio in your application; Harness forwards the native format. Supply realtime options through `model_settings`. For manual turns, use `commit_audio()`, `clear_audio()`, and `create_response()` where the native profile supports them. Unsupported operations raise an error.

When the user interrupts, stop local playback and discard buffered audio. Then choose one position convention:

| Call                          | Position supplied by your player                                                   |
| ----------------------------- | ---------------------------------------------------------------------------------- |
| `interrupt(played_ms=...)`    | Actual playback position in milliseconds                                           |
| `interrupt(played_bytes=...)` | Cumulative PCM bytes actually played, with exactly one `stream_audio()` subscriber |
| `interrupt()`                 | Cancel without truncating at a playback position                                   |

The millisecond and byte arguments are mutually exclusive. Native byte accounting handles unread audio and reports whether an interruption occurred. Interruption keeps the Run open and leaves completed tool effects intact.

Keep `audio_retention="transcript_only"` for transcript history. Choose `input_audio`, `output_audio`, or `all` when the Host needs those bytes in exported messages. Raw speech audio is stripped from Harness semantic events and producer observations; playback uses the separate audio stream.

## Tools, approvals, and context

Configure tools and permissions before opening the connection. Native tool schemas are advertised at connection time. Local preparation and policy still check later calls, but newly added schemas are not advertised mid-session. `CodeActCapability` uses its prepared tool directory and supports explicit `store` and `load` values as in ordinary Runs.

For approval or externally supplied results, install native `HandleDeferredToolCalls` in the Agent's capabilities or current Run bindings. Its handler can await a Host decision while the voice connection stays open. Rejection prevents tool execution; Run cancellation cancels the waiting handler. Without a handler, an unresolved call becomes a native failed tool result. Design the decision UI around this inline wait, rather than the durable `DeferredToolResume` workflow used by ordinary Runs.

Harness projects initial model context into connection instructions. After a successful local tool or resolved external result, it appends refreshed context before the native session sends that result and continues. Original values, evidence, and metadata remain available. Use realtime events to follow conversational turns; graph, model-request, and output-validation hooks do not serve as turn hooks.

## Completion and continuation

Close the session, drain the iterator, then persist state. These actions have distinct meanings:

| Action or event               | Meaning                                                            |
| ----------------------------- | ------------------------------------------------------------------ |
| Conversational turn complete  | The session can accept another turn                                |
| `close()`                     | End the native session normally                                    |
| `cancel()`                    | Request a cancelled Run                                            |
| Final `HarnessRunResultEvent` | Receive the result after cleanup                                   |
| Early async-context exit      | Close resources and retain a checkpoint without a terminal receipt |

Provider, plugin, and cleanup errors propagate to the application. Failed teardown produces no successful terminal receipt. Live conversation content lives in messages: `result.output` is always `None`, even when the Agent's ordinary `output_type` is `str`.

Continue with the saved JSON and the `exchange` helper above. Add this helper to the same module and call it from your application's async entry point with an Agent, native Model, and previously saved state:

```python
async def continue_live(agent, model, saved_json):
    previous = HarnessState.model_validate_json(saved_json)
    current = await exchange(
        agent, model, "Continue our conversation.", previous_state=previous,
    )
    assert current.thread_id == previous.thread_id
    return current.model_dump_json()
```

The Host saves the returned JSON using its own persistence mechanism. `export_state()` includes accepted native history, including sent text that has not received a reply. A continuation opens a new connection with a fresh Run ID and bindings. History seeding requires native Model support. Explicit stored values survive; sockets, approval waiters, player buffers, and temporary CodeAct variables belong to the old connection or Run.

## Usage and limits

Use `live.usage`, terminal usage, or `UsageSnapshot` for attributed Run accounting. Live updates response contributions during execution and settles them after closure without counting them twice. Native usage limits and Harness attributed-budget checks apply. A caller's native usage baseline is excluded from new Run-local contributions.

The coverage marker is `model_usage_coverage="responses_only"`: it includes observed model responses and separately attributed auxiliary work and provider receipts. Session-only transcription, duration, or other provider consumption may be missing. Reconcile complete billing with provider evidence; unknown costs remain unknown and a known partial cost is not a full-session quote.

Use `live.native_usage` only to diagnose the native accumulator. This detached copy can include session-only usage, a caller baseline, and nested work sharing the accumulator. Adding it to attributed usage would mix accounting scopes. It is not an attributed receipt and is not restored from portable state.

Live rejects `RunBindings.model_call_check` before connecting with `live_model_check_unsupported`: native realtime has no equivalent of the ordinary Host per-request reservation boundary. See [usage and limits](usage-and-limits.md) for ordinary accounting and [state and resume](state-and-resume.md) for persistence.

## Connect Codex voice

Use `CodexLiveModel` when the speech connection delegates work to your Host's existing Agent execution workflow. The factory creates the voice call and control connection. Your application owns the WebRTC media peer, task admission, Agent Runs, persistence, and presentation.

1. Import `CodexLiveModel` from `a13n_harness.models.codex_live`. Construct it with an `OpenAICodexCredentialSource` from your [authentication integration](model-authentication.md). The default voice is `cove`; other values must be supported `CodexLiveVoice` V3 names.
2. Create a WebRTC offer in your media peer. Enter `model.connect(offer_sdp, instructions=..., on_answer=..., initial_items=...)` as an async context manager. Supply prior context as an optional sequence of `ConversationItem` values.
3. In the async `on_answer(answer_sdp)` callback, apply the answer and wait for the peer to connect. The callback must finish before the control connection attaches. Keep subscription credentials in the Host; the browser receives the SDP answer.
4. Consume `session.events()`. Route each event through your application as below.
5. Exit the context manager to close the voice connection, and stop the media peer in your Host cleanup.

| Event                                | Application action                                                                               |
| ------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `SessionReady`                       | Display provider readiness                                                                       |
| `Transcript(role, text, final)`      | Show speech text and distinguish interim from final text                                         |
| `Delegation(item_id, text, context)` | Decide whether to admit the task, then submit its text and bounded context to the Agent workflow |

Return selected user-facing results through `session.append_speech(text, delegation_id=...)` and progress through `session.append_status(...)`. The optional ID associates text with the delegation. These writes supply speech context; task completion, audible playback, and durable delivery need their own application evidence. Send user-facing text, keeping private reasoning out of both channels.

Each connection creates at most one call. When bootstrap or a context write fails ambiguously, handle the error and let the user start a new voice session; the factory does not replay those operations. Connection still requires account entitlement and provider availability.
