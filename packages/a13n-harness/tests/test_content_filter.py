from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a13n_harness import (
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.filters import (
    ContentFilterCapability,
    ContentFilterConfiguration,
)
from pydantic_ai import BinaryContent, ImageUrl, TextContent, ToolReturn
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
            and len(part.content) == 2
            and all(
                isinstance(item, TextContent) and item.metadata.get("source_id") == "a13n.tool" for item in part.content
            )
        ][-1]
        assert "URL is unsafe" in filtered_content[0].content
        assert "exceeds request limits" in filtered_content[1].content
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
    result = await executable.run([safe_image], bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert safe_image.url == "https://example.com/safe.png"
    assert unsafe_image.url.endswith("api_key=secret")
    assert oversized_binary.data == b"1234"


@pytest.mark.parametrize("display", [False, True])
@pytest.mark.parametrize(
    ("tool_result", "shape"), [(False, "list"), (False, "tuple"), (True, "scalar"), (True, "list"), (True, "tuple")]
)
async def test_filter_replacements_preserve_user_metadata_without_changing_tool_values(
    display: bool,
    shape: str,
    tool_result: bool,
) -> None:
    from typing import Any, cast

    from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
    from pydantic_ai.models.test import TestModel

    metadata = {"display": display, "source_id": "original-input", "application": {"keep": True}}
    media = BinaryContent(b"1234", media_type="image/png", vendor_metadata=metadata)
    content = media if shape == "scalar" else (media,) if shape == "tuple" else [media]
    part = ToolReturnPart("view", content, tool_call_id="view-1") if tool_result else UserPromptPart(content)
    request = ModelRequest(parts=[part], metadata={"keep": "request"})
    context = ModelRequestContext(
        model=TestModel(),
        messages=[request],
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )
    filtered = await ContentFilterCapability(ContentFilterConfiguration(max_binary_bytes=3)).before_model_request(
        cast(Any, None),
        context,
    )
    replacement = filtered.messages[0].parts[0].content
    if shape == "list":
        assert isinstance(replacement, list)
        replacement = replacement[0]
    elif shape == "tuple":
        assert isinstance(replacement, tuple)
        replacement = replacement[0]
    if tool_result:
        assert isinstance(replacement, str)
        assert "exceeds request limits" in replacement
    else:
        assert isinstance(replacement, TextContent)
        assert replacement.metadata == metadata
        assert replacement.metadata is not metadata
        assert replacement.metadata["application"] is not metadata["application"]
    assert filtered.messages[0].metadata == {"keep": "request"}
    assert request.parts[0] is part
    assert media.vendor_metadata == metadata
