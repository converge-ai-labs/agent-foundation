from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a13n_harness import (
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.content import ContentItem, ContentMetadata, annotate_prompt, prompt_content
from a13n_harness.filters import (
    ContentFilterCapability,
    ContentFilterConfiguration,
)
from a13n_harness.tools._output import tool_execution_value
from pydantic_ai import BinaryContent, ImageUrl, RunContext, TextContent, ToolReturn
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RunUsage

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
        assert (
            tool_execution_value(returned[-1].content, returned[-1].metadata) == "ordinary tool text remains ordinary"
        )
        filtered_content = returned[-1].content[1:]
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
    result = await executable.run([safe_image], bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert result.state is not None
    persisted = str(result.state.message_history)
    assert "api_key=secret" not in persisted
    assert "URL is unsafe" in persisted
    assert "exceeds request limits" in persisted
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

    from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
    from pydantic_ai.models.test import TestModel

    metadata = {"display": display, "source_id": "original-input", "application": {"keep": True}}
    media = BinaryContent(b"1234", media_type="image/png", vendor_metadata={"detail": "high"})
    content = media if shape == "scalar" else (media,) if shape == "tuple" else [media]
    part = ToolReturnPart("view", content, tool_call_id="view-1") if tool_result else UserPromptPart(content)
    request = ModelRequest(parts=[part], metadata={"keep": "request"})
    if not tool_result:
        request = annotate_prompt(request, 0, [ContentItem(media, ContentMetadata.model_validate(metadata))])
    context = ModelRequestContext(
        model=TestModel(),
        messages=[request],
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )
    filtered = await ContentFilterCapability(ContentFilterConfiguration(max_binary_bytes=3)).before_model_request(
        RunContext(deps=None, model=context.model, usage=RunUsage(), messages=list(context.messages)),
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
        annotation = prompt_content(filtered.messages[0], 0)[0].metadata
        assert annotation.model_dump(exclude_defaults=True) == ContentMetadata.model_validate(metadata).model_dump(
            exclude_defaults=True
        )
        annotation.model_extra["application"]["keep"] = False
        assert prompt_content(request, 0)[0].metadata.model_extra["application"] == {"keep": True}
    assert filtered.messages[0].metadata["keep"] == "request"
    assert request.parts[0] is part
    assert media.vendor_metadata == {"detail": "high"}
