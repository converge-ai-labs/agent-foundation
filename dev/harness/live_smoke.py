"""Run one real-provider Live exchange, including a tool and separate audio drain.

From the repository root, with OPENAI_API_KEY exported:
    uv run --locked python dev/harness/live_smoke.py --model YOUR_REALTIME_MODEL
"""

from __future__ import annotations

import argparse
import asyncio
import os

from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, HarnessEvent, HarnessRunResultEvent, HarnessState
from a13n_harness.live import HarnessLiveStream
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import PartEndEvent, RealtimeTurnCompleteEvent, SpeechPart, TextPart
from pydantic_ai.realtime.openai import OpenAIRealtimeModel


async def drain_audio(live: HarnessLiveStream) -> int:
    """Consume PCM without assuming a particular audio device is installed."""
    total = 0
    async for chunk in live.stream_audio():
        total += len(chunk)
    return total


async def main(model_name: str) -> None:
    calls: list[int] = []

    def double(value: int) -> int:
        """Double an integer. Use this tool for the requested calculation."""
        calls.append(value)
        return value * 2

    executable = HarnessBuilder().build(
        AgentDefinition(
            output_type=str,
            agent=AgentSpec(instructions="Call double exactly once, then speak the result briefly."),
            capabilities=(Capability(tools=[double]),),
        )
    )
    terminals = 0
    async with asyncio.timeout(60), executable.live(model=OpenAIRealtimeModel(model_name)) as live:
        async with asyncio.TaskGroup() as tasks:
            audio = tasks.create_task(drain_audio(live))
            await live.send("Use double to calculate twice 21, then say the answer.", respond=True)
            async for item in live:
                if isinstance(item, HarnessEvent):
                    event = item.event
                    if isinstance(event, PartEndEvent):
                        if isinstance(event.part, SpeechPart) and event.part.transcript:
                            print(event.part.transcript)
                        elif isinstance(event.part, TextPart):
                            print(event.part.content)
                    if isinstance(event, RealtimeTurnCompleteEvent):
                        await live.close()
                elif isinstance(item, HarnessRunResultEvent):
                    terminals += 1
                    assert item.result.status == "completed", item.result.failure
        state = HarnessState.model_validate_json((await live.export_state()).model_dump_json())
        assert state.thread_id == live.thread_id
        assert terminals == 1 and calls == [21], (terminals, calls)
        print(
            f"completed: tool_calls={len(calls)}, audio_bytes={audio.result()}, messages={len(state.message_history)}"
        )
        print(f"attributed_usage={live.usage.model_dump_json()}")
        print(f"native_usage={live.native_usage}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="An OpenAI realtime model available to your account")
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("Export OPENAI_API_KEY before running this real-provider smoke test.")
    asyncio.run(main(args.model))
