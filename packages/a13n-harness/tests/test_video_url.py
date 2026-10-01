from __future__ import annotations

import json
from collections.abc import AsyncIterator
from copy import deepcopy

import pytest
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessModelCharacteristics,
    ModelCapability,
    RunBindings,
    RunConfiguration,
)
from a13n_harness._video_urls import video_url_error
from a13n_harness.filters.video_url import project_video_urls
from a13n_harness.tools import (
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolPermissions,
    ToolPermissionsCapability,
)
from pydantic_ai import BinaryContent, VideoUrl
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio
YOUTUBE = "https://www.youtube.com/watch?v=9hE5-98ZeCg"
VIDEO = "https://cdn.example.com/video.mp4"


def _characteristics(*capabilities: str) -> HarnessModelCharacteristics:
    return HarnessModelCharacteristics(
        capabilities=frozenset(ModelCapability(value) for value in capabilities if value != "youtube"),
        url_input={"video": ["youtube"] if "youtube" in capabilities else []},
    )


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        return InvocationPolicyDecision.allow()


def _videos(messages) -> list[VideoUrl]:
    return [
        item
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, (UserPromptPart, ToolReturnPart))
        for item in (part.content if isinstance(part.content, (list, tuple)) else [part.content])
        if isinstance(item, VideoUrl)
    ]


@pytest.mark.parametrize(
    ("capabilities", "url", "expected"),
    [
        ((), YOUTUBE, "video_url_unsupported"),
        (("video_understanding",), YOUTUBE, "video_url_unsupported"),
        (("youtube",), YOUTUBE, None),
        (("youtube",), "https://youtu.be/9hE5-98ZeCg", None),
        (("youtube",), VIDEO, "video_url_requires_materialization"),
        (("video_understanding",), VIDEO, "video_url_requires_materialization"),
        (("youtube",), "https://youtube.com.evil.example/video.mp4", "video_url_requires_materialization"),
        (("youtube",), "file:///video.mp4", "video_url_invalid"),
        (("youtube",), "https://user:secret@example.com/video.mp4", "video_url_invalid"),
        (("youtube",), "https://example.com/video.mp4?apikey=secret", "video_url_invalid"),
        (("youtube",), "https://example.com/video.mp4#secret", "video_url_invalid"),
    ],
)
def test_video_url_admission_is_explicit_and_provider_neutral(capabilities, url, expected) -> None:
    assert video_url_error(VideoUrl(url), _characteristics(*capabilities)) == expected


@pytest.mark.parametrize("force_download", [True, "allow-local"])
def test_video_urls_cannot_request_a_download(force_download) -> None:
    assert (
        video_url_error(VideoUrl(VIDEO, force_download=force_download), _characteristics("video_understanding"))
        == "video_url_invalid"
    )


@pytest.mark.parametrize("capabilities", [(), ("video_understanding",), ("youtube",), ("video_understanding",)])
async def test_default_tool_visibility_follows_model_traits(capabilities) -> None:
    seen = []

    async def respond(messages, info):
        seen.append((deepcopy(messages), info))
        yield "done"

    agent = HarnessBuilder().build(
        AgentSpec(model_characteristics=_characteristics(*capabilities)),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    assert (await agent.run("Inspect the video")).output_or_raise() == "done"
    names = {tool.name for tool in seen[0][1].function_tools}
    expected = bool(set(capabilities) & {"youtube", "video_understanding"})
    assert ("read_video_url" in names) is expected
    guidance = repr(seen[0][0]) + str(seen[0][1].instructions)
    assert ('<tool-instruction name="read_video_url">' in guidance) is expected


@pytest.mark.parametrize(("url", "media_type", "error"), [(YOUTUBE, None, None)])
async def test_read_video_url_attaches_native_content_without_a_reader(url, media_type, error) -> None:
    seen = []

    async def stream(messages, info) -> AsyncIterator[str | DeltaToolCalls]:
        seen.append(deepcopy(messages))
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield "done"
        else:
            yield {
                0: DeltaToolCall(
                    name="read_video_url",
                    json_args=json.dumps(
                        {"url": url, "media_type": media_type, "instructions": "Summarize speech with timestamps."}
                    ),
                    tool_call_id="video-1",
                )
            }

    agent = HarnessBuilder().build(
        AgentSpec(model_characteristics=_characteristics("video_understanding", "youtube")),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    result = await agent.run(
        "Inspect", bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_Allow()),))
    )
    assert result.output_or_raise() == "done"
    videos = _videos(seen[-1])
    if error:
        assert not videos
        assert error in repr(seen[-1])
    else:
        assert len(videos) == 1
        assert videos[0].url == url
        assert videos[0].force_download is False
        assert videos[0].vendor_metadata is None
        assert "Summarize speech with timestamps." in repr(seen[-1])
    assert not any(
        isinstance(item, BinaryContent)
        for message in seen[-1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, (list, tuple))
        for item in part.content
    )


@pytest.mark.parametrize("surface", ["user-list", "user-tuple", "tool-list", "tool-scalar"])
def test_url_filter_preserves_history_and_other_content(surface) -> None:
    supported = VideoUrl(YOUTUBE, identifier="youtube", vendor_metadata={"fps": 1.0})
    unsupported = VideoUrl(VIDEO)
    contents = ["before", supported, unsupported, BinaryContent(b"video", media_type="video/mp4"), "after"]
    part = (
        UserPromptPart(contents if surface == "user-list" else tuple(contents))
        if surface.startswith("user")
        else ToolReturnPart("video", contents if surface == "tool-list" else unsupported, tool_call_id="video-1")
    )
    history = [ModelRequest(parts=[part])]
    snapshot = deepcopy(history)
    projected = project_video_urls(history, _characteristics("youtube"))
    assert projected is not None
    assert history == snapshot
    assert len(_videos(projected)) == (0 if surface == "tool-scalar" else 1)
    assert "filtered-content" in repr(projected)
    if surface != "tool-scalar":
        assert _videos(projected)[0].identifier == "youtube"
        assert _videos(projected)[0].vendor_metadata == {"fps": 1.0}
        assert "before" in repr(projected) and "after" in repr(projected)
    assert project_video_urls(history, _characteristics("youtube", "video_understanding")) is not None


async def test_model_switch_filters_request_but_retains_native_history() -> None:
    observed = []

    async def respond(messages, info):
        observed.append(deepcopy(messages))
        yield "done"

    def build(characteristics):
        return HarnessBuilder().build(
            AgentSpec(model_characteristics=characteristics),
            output_type=str,
            model=FunctionModel(stream_function=respond),
        )

    first = await build(_characteristics("youtube")).run(["Inspect", VideoUrl(YOUTUBE)])
    second = await build(_characteristics()).run("Continue", previous_state=first.state)
    assert _videos(observed[0])
    assert not _videos(observed[-1])
    assert _videos(second.state.message_history)


async def test_default_tool_still_obeys_permission_denial() -> None:
    seen = []

    async def stream(messages, info):
        seen.append(deepcopy(messages))
        if len(seen) > 1:
            yield "done"
        else:
            yield {
                0: DeltaToolCall(name="read_video_url", json_args=json.dumps({"url": YOUTUBE}), tool_call_id="denied-1")
            }

    agent = HarnessBuilder().build(
        AgentSpec(model_characteristics=_characteristics("youtube")),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(ToolPermissionsCapability(ToolPermissions(rules={"media.read_video_url": "deny"})),),
    )
    result = await agent.run("Inspect")
    assert result.output_or_raise() == "done"
    assert not _videos(seen[-1])
    assert "denied" in repr(seen[-1]).lower()


async def test_google_adapter_projects_actual_tool_content_as_a_file_reference(monkeypatch) -> None:
    from types import SimpleNamespace

    from a13n_harness.tools._output import TOOL_CONTENT_METADATA_KEY, _apply_result_policy, _render_tool_return
    from a13n_harness.tools.metadata import ToolOutputPolicy
    from a13n_harness.toolsets.video_url import VideoUrlToolset
    from google.genai.types import Part
    from pydantic_ai import ToolReturn
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    async def no_download(*args, **kwargs):
        raise AssertionError("native YouTube URL must not download")

    monkeypatch.setattr("pydantic_ai.models.google.download_item", no_download)
    model = GoogleModel("gemini-2.5-pro", provider=GoogleProvider(api_key="test-only-not-a-real-key"))
    result = await VideoUrlToolset().read_video_url(
        SimpleNamespace(
            deps=SimpleNamespace(model_characteristics=_characteristics("youtube"), configuration=RunConfiguration())
        ),
        YOUTUBE,
    )
    assert isinstance(result, ToolReturn)
    settled = await _apply_result_policy(
        result, ToolOutputPolicy(max_inline_bytes=256 * 1024, max_output_bytes=256 * 1024)
    )
    assert isinstance(settled, ToolReturn)
    settled = _render_tool_return(settled)
    video = settled.return_value[1]
    assert isinstance(video, VideoUrl)
    assert settled.metadata[TOOL_CONTENT_METADATA_KEY]["items"][0]["display"] is False
    part = await model._map_file_to_part(video)
    assert part["file_data"] == {"file_uri": YOUTUBE, "mime_type": "video/mp4"}
    # Exercise Google SDK validation, not only the adapter's unvalidated dict.
    validated = Part.model_validate(part)
    assert validated.file_data.file_uri == YOUTUBE
    assert validated.video_metadata is None


def test_filter_does_not_copy_unmodified_requests(monkeypatch) -> None:
    def no_copy(*args, **kwargs):
        raise AssertionError("unchanged requests should not copy their history")

    monkeypatch.setattr("a13n_harness.filters.video_url.deepcopy", no_copy)
    assert project_video_urls([ModelRequest(parts=[UserPromptPart("text")])], None) is None
    assert (
        project_video_urls(
            [ModelRequest(parts=[UserPromptPart([VideoUrl(YOUTUBE)])])],
            _characteristics("youtube"),
        )
        is None
    )


async def test_non_streaming_request_filters_urls_without_rewriting_history() -> None:
    from types import SimpleNamespace

    from a13n_harness.capabilities.video_url import VideoUrlCapability
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart

    seen = []

    def respond(messages, info):
        seen.append(deepcopy(messages))
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(respond), capabilities=[VideoUrlCapability()])
    result = await agent.run(
        ["Inspect", VideoUrl(YOUTUBE)],
        deps=SimpleNamespace(model_characteristics=_characteristics(), toolset_instructions=True),
    )
    assert result.output == "done"
    assert not _videos(seen[0])
    assert _videos(result.all_messages())


@pytest.mark.parametrize("source", ["definition", "run"])
def test_video_url_owner_cannot_be_replaced_by_caller_capabilities(source) -> None:
    from a13n_harness.capabilities import VideoUrlCapability
    from a13n_harness.errors import DefinitionError

    with pytest.raises(DefinitionError) as exc:
        if source == "run":
            executable = HarnessBuilder().build(AgentSpec(), output_type=str)
            executable.stream("Inspect", bindings=RunBindings.embedded(capabilities=(VideoUrlCapability(),)))
        else:
            HarnessBuilder().build(AgentSpec(), output_type=str, capabilities=(VideoUrlCapability(),))
    assert exc.value.code == "capability_scope_invalid"


def test_request_only_url_projection_preserves_prompt_annotations() -> None:
    from a13n_harness.content import ContentItem, ContentMetadata, input_request, prompt_content

    items = [
        ContentItem("before", ContentMetadata(source_id="authored-before")),
        ContentItem(VideoUrl(YOUTUBE), ContentMetadata(display=False, source_id="video", file_id="file-one")),
        ContentItem("after", ContentMetadata(source_id="authored-after")),
    ]
    request = input_request(items, metadata={"application": {"reference": "retained"}})
    original = deepcopy(request)
    projected = project_video_urls([request], _characteristics())
    assert projected is not None
    annotated = prompt_content(projected[0], 0)
    assert [item.metadata for item in annotated] == [item.metadata for item in items]
    assert "filtered-content" in annotated[1].value
    assert projected[0].metadata["application"] == {"reference": "retained"}
    assert request == original and prompt_content(request, 0) == items
