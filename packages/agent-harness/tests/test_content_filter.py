from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a13n_harness import (
    ContentFilterCapability,
    ContentFilterConfiguration,
    HarnessBuilder,
    RunBindings,
)
from pydantic_ai import BinaryContent, ImageUrl, ToolReturn
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


async def test_content_filter_handles_user_and_tool_return_media_without_text_spill() -> None:
    safe_image = ImageUrl("https://example.com/safe.png")
    unsafe_image = ImageUrl("https://example.com/private.png?api_key=secret")
    oversized_binary = BinaryContent(b"1234", media_type="image/png")
    calls = 0

    def attach() -> ToolReturn:
        return ToolReturn(
            "ordinary tool text remains ordinary",
            content=[unsafe_image, oversized_binary],
        )

    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        del info
        calls += 1
        if calls == 1:
            prompts = [
                part.content
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            ]
            assert any(
                isinstance(content, list | tuple)
                and len(content) == 1
                and isinstance(content[0], ImageUrl)
                and content[0].url == safe_image.url
                for content in prompts
            )
            yield {0: DeltaToolCall(name="attach", json_args="{}", tool_call_id="attach-1")}
            return

        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if type(part) is ToolReturnPart
        ]
        assert returned[-1].content == "ordinary tool text remains ordinary"
        filtered_content = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            and isinstance(part.content, list)
            and part.content
            and all(isinstance(item, str) for item in part.content)
        ][-1]
        assert "URL is unsafe" in filtered_content[0]
        assert "exceeds request limits" in filtered_content[1]
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            ContentFilterCapability(
                ContentFilterConfiguration(
                    accepted_media=frozenset({"image"}),
                    max_media_items=2,
                    max_binary_bytes=3,
                )
            ),
            Capability(tools=[attach], id="content-filter-tools"),
        ),
    )
    result = await executable.run([safe_image], bindings=RunBindings.local())

    assert result.output_or_raise() == "done"
    assert safe_image.url == "https://example.com/safe.png"
    assert unsafe_image.url.endswith("api_key=secret")
    assert oversized_binary.data == b"1234"
