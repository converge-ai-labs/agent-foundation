from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from a13n_harness import HarnessBuilder, HarnessEvent, RunBindings
from a13n_harness.capabilities import NativeImageGenerationCapability
from a13n_harness.context import AgentContext
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import NativeTool
from pydantic_ai.messages import FilePart, NativeToolReturnPart, PartStartEvent, TextPart
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.native_tools import ImageGenerationTool, WebSearchTool
from pydantic_ai.providers.openai import OpenAIProvider

pytestmark = pytest.mark.anyio
_IMAGE = b"generated-image-content"


def _sse() -> bytes:
    response: dict[str, Any] = {
        "id": "resp_image",
        "created_at": 1,
        "model": "gpt-4.1",
        "object": "response",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "status": "in_progress",
    }
    item = {
        "id": "ig_1",
        "type": "image_generation_call",
        "status": "completed",
        "result": base64.b64encode(_IMAGE).decode(),
    }
    events = [
        {"type": "response.created", "sequence_number": 0, "response": response},
        {
            "type": "response.image_generation_call.partial_image",
            "sequence_number": 1,
            "item_id": "ig_1",
            "output_index": 0,
            "partial_image_index": 0,
            "partial_image_b64": base64.b64encode(b"preview").decode(),
        },
        {"type": "response.output_item.done", "sequence_number": 2, "output_index": 0, "item": item},
        {
            "type": "response.completed",
            "sequence_number": 3,
            "response": {**response, "output": [item], "status": "completed"},
        },
    ]
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


async def test_native_image_stream_saves_final_image_and_resumes_without_bytes(tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []
    saved: list[bytes] = []

    def transport(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse())

    async def save(ctx: RunContext[AgentContext], image: FilePart) -> str:
        saved.append(image.content.data)
        path = tmp_path / "generated.png"
        path.write_bytes(image.content.data)
        return path.as_posix()

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        model = OpenAIResponsesModel("gpt-4.1", provider=OpenAIProvider(api_key="test", http_client=client))
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=model,
            capabilities=(
                NativeImageGenerationCapability(saver=save, tool=ImageGenerationTool(quality="low")),
                NativeTool(WebSearchTool()),
            ),
        )
        bindings = RunBindings.embedded()
        stream = executable.stream("Draw a tree", bindings=bindings)
        async with stream:
            events = [item.event async for item in stream if isinstance(item, HarnessEvent)]
        result = stream.result
        assert result is not None and result.state is not None
        assert saved == [_IMAGE]
        assert "generated.png" in result.output
        assert (tmp_path / "generated.png").read_bytes() == _IMAGE
        assert not any(isinstance(part, FilePart) for message in result.all_messages() for part in message.parts)
        assert any(
            isinstance(part, NativeToolReturnPart) for message in result.all_messages() for part in message.parts
        )
        assert base64.b64encode(_IMAGE).decode() not in result.state.model_dump_json()
        assert all(not isinstance(event.part, FilePart) for event in events if isinstance(event, PartStartEvent))
        assert any(
            isinstance(event, PartStartEvent)
            and isinstance(event.part, TextPart)
            and "generated.png" in event.part.content
            for event in events
        )
        assert {tool["type"] for tool in requests[0]["tools"]} == {"image_generation", "web_search"}
        assert "![brief image description](<saved path or URL>)" in json.dumps(requests[0])
        assert "Use the exact path or URL returned by the image saver" in json.dumps(requests[0])

        await executable.run("Another one", previous_state=result.state, bindings=RunBindings.embedded())
        assert "generated.png" in json.dumps(requests[1]["input"])
        assert base64.b64encode(_IMAGE).decode() not in json.dumps(requests[1]["input"])


async def test_native_image_save_failure_is_not_success() -> None:
    async def save(ctx: RunContext[AgentContext], image: FilePart) -> str:
        raise OSError("storage unavailable")

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse())
        )
    ) as client:
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=OpenAIResponsesModel("gpt-4.1", provider=OpenAIProvider(api_key="test", http_client=client)),
            capabilities=(NativeImageGenerationCapability(saver=save),),
        )
        stream = executable.stream("Draw", bindings=RunBindings.embedded())
        async with stream:
            async for _ in stream:
                pass
        result = stream.result
        assert result is not None and result.status == "failed" and result.output is None
        assert not any(isinstance(part, FilePart) for message in result.all_messages() for part in message.parts)


@pytest.mark.parametrize("cancel", [False, True])
async def test_interrupted_image_preview_never_enters_events_or_continuation(cancel: bool) -> None:
    import asyncio
    from collections.abc import AsyncIterator

    waiting = asyncio.Event()
    saved: list[bytes] = []

    class PreviewStream(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"\n\n".join(_sse().split(b"\n\n")[:2]) + b"\n\n"
            waiting.set()
            if cancel:
                await asyncio.Event().wait()
            raise RuntimeError("provider stream interrupted")

    async def save(ctx: RunContext[AgentContext], image: FilePart) -> str:
        saved.append(image.content.data)
        return "/tmp/image.png"

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=PreviewStream())
        )
    ) as client:
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=OpenAIResponsesModel("gpt-4.1", provider=OpenAIProvider(api_key="test", http_client=client)),
            capabilities=(NativeImageGenerationCapability(saver=save),),
        )
        stream = executable.stream("Draw", bindings=RunBindings.embedded())
        async with stream:

            async def consume():
                return [item.event async for item in stream if isinstance(item, HarnessEvent)]

            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(waiting.wait(), timeout=5)
            if cancel:
                stream.cancel()
            events = await asyncio.wait_for(consumer, timeout=5)
        result = stream.result
        assert result is not None and result.status == ("cancelled" if cancel else "failed")
        assert saved == []
        assert not any(isinstance(part, FilePart) for message in result.all_messages() for part in message.parts)
        assert not any(isinstance(event, PartStartEvent) and isinstance(event.part, FilePart) for event in events)
