"""Fixed interface hints stay model-visible without becoming authored input."""

from copy import deepcopy
from itertools import pairwise
from xml.etree import ElementTree

import pytest
from a13n_harness.content import input_request, request_input_content
from a13n_harness.errors import InputError
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.surface_context import append_surface_provenance, surface_context
from a13n_harness_ui.surfaces import RootOperationStatus
from a13n_harness_ui.thread_files import ComposerInput
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, TextContent, TextPart

from .test_long_text_input import _app, _function_model, _references, _text_parts


def _hints(messages):
    return [
        TextContent(item.value, metadata=item.metadata.model_dump(mode="json"))
        for message in messages
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if item.metadata.source_id == "a13n-harness-ui.surface" and isinstance(item.value, str)
    ]


@pytest.mark.parametrize("surface", ["tui", "webui"])
def test_surface_hint_preserves_native_parts_and_uses_hidden_xml(surface):
    text = TextContent("Compare this", metadata={"source_id": "authored", "custom": {"index": 1}})
    image = BinaryContent(data=b"pixels", media_type="image/png")
    prompt = [text, image, " then explain"]
    before = deepcopy(prompt)

    result = append_surface_provenance(prompt, surface)

    assert prompt == before
    assert result[:-1] == tuple(prompt)
    assert result[0] is text and result[1] is image
    origin = result[-1]
    assert isinstance(origin, TextContent)
    assert origin.metadata == {"display": False, "source_id": "a13n-harness-ui.input-surface", "input_surface": surface}
    assert "This input was submitted" in origin.content
    hint = surface_context([input_request(result)])[0]
    element = ElementTree.fromstring(hint.content)
    assert element.tag == "surface-context"
    assert element.attrib == {"source": "a13n-harness-ui"}
    assert "<![CDATA[" in hint.content
    assert f"Harness UI {surface.upper() if surface == 'tui' else 'WebUI'}" in element.text
    if surface == "webui":
        assert "static diagrams" in element.text
        assert "LaTeX math renders with KaTeX" in element.text
        assert "/threads/{root_thread_id}?native=files&native_path=" in element.text
    else:
        assert "as source text rather than diagrams" in element.text
        assert "does not restrict formats written to files" in element.text
    assert append_surface_provenance("hello", surface)[0] == "hello"


@pytest.mark.anyio
async def test_surface_guidance_changes_only_on_transition_and_provenance_is_per_submission(tmp_path, monkeypatch):
    observed = []

    def model(messages, info):
        observed.append(deepcopy(messages))
        return ModelResponse(parts=[TextPart("Received")])

    async with _app(tmp_path, monkeypatch, _function_model(model), threshold=10) as (app, _, _):
        thread = await app.create_thread()
        for surface in (None, "webui", "webui", "tui", "tui", "webui", None):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello", input_surface=surface)
            outcome = await app.wait_root_operation(receipt.receipt_id)
            assert outcome.status is RootOperationStatus.completed, outcome.failure

        assert [len(_hints(history)) for history in observed] == [1, 2, 2, 3, 3, 4, 5]
        assert "no declared response surface" in _hints(observed[0])[0].content
        assert "Harness UI WebUI" in _hints(observed[1])[-1].content
        assert "Harness UI TUI" in _hints(observed[3])[-1].content
        assert "Harness UI WebUI" in _hints(observed[5])[-1].content
        assert "no declared response surface" in _hints(observed[6])[-1].content
        assert not _references(observed[-1])
        for previous, current in pairwise(observed):
            # Cross-Run instruction capture can change; committed surface blocks cannot.
            assert _hints(current)[: len(_hints(previous))] == _hints(previous)
        origins = [
            part
            for part in _text_parts(observed[-1])
            if isinstance(part, TextContent)
            and (part.metadata or {}).get("source_id") == "a13n-harness-ui.input-surface"
        ]
        assert len(origins) == 7

        page = await app.get_thread_transcript(thread_id=thread.thread_id)
        hints = [
            part
            for entry in page.entries
            for part in entry.parts
            if part.metadata.source_id == "a13n-harness-ui.surface"
        ]
        assert len(hints) == 5
        assert all(part.metadata.display is False for part in hints)
        assert all(
            "surface-context" not in (part.text or "")
            for entry in page.entries
            for part in entry.parts
            if part.metadata.display
        )


@pytest.mark.anyio
@pytest.mark.parametrize("surface", ["tui", "webui"])
@pytest.mark.parametrize(
    "prompt,error",
    [
        ("", RunCoordinationError),
        ("  ", RunCoordinationError),
        ((), InputError),
        (ComposerInput(parts=()), ValueError),
        (ComposerInput(parts=("  ",)), ValueError),
    ],
)
async def test_surface_hint_does_not_make_empty_input_valid(tmp_path, monkeypatch, surface, prompt, error):
    def model(messages, info):
        pytest.fail("Rejected input must not reach the model")

    async with _app(tmp_path, monkeypatch, _function_model(model)) as (app, _, _):
        thread = await app.create_thread()
        with pytest.raises(error, match=r"blank|empty"):
            await app.submit_thread(thread_id=thread.thread_id, prompt=prompt, input_surface=surface)


def test_surface_provenance_survives_retained_input_replay_and_internal_messages_do_not_clear_it():
    from a13n_harness.content import ContentItem, ContentMetadata
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    web = input_request(append_surface_provenance("question", "webui"))
    history = [web, input_request("internal follow-up")]
    assert "Harness UI WebUI" in surface_context(history)[0].content
    # Compaction/handoff replays semantic retained inputs, not generated guidance.
    origin = request_input_content(web)[-1]
    retained = input_request([ContentItem(origin.value, origin.metadata), "summary"])
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json([retained]))
    assert surface_context(restored) == surface_context(history)
    steering = input_request([ContentItem("adjust", ContentMetadata.model_validate({"a13n.steering-source": "user"}))])
    assert surface_context([*restored, steering]) == surface_context(history)
    tui = input_request(append_surface_provenance("next", "tui"))
    assert "Harness UI TUI" in surface_context([*restored, tui])[0].content
    unspecified = input_request(append_surface_provenance("next", None))
    assert "no declared response surface" in surface_context([*restored, tui, unspecified])[0].content
    assert not surface_context([input_request("summary without retained inputs")])
