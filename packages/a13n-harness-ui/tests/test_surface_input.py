"""Fixed interface hints stay model-visible without becoming authored input."""

from copy import deepcopy
from xml.etree import ElementTree

import pytest
from a13n_harness.errors import InputError
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.root_input import append_surface_hint
from a13n_harness_ui.surfaces import RootOperationStatus
from a13n_harness_ui.thread_files import ComposerInput
from pydantic_ai.messages import BinaryContent, ModelResponse, TextContent, TextPart

from .test_long_text_input import _app, _function_model, _references, _text_parts


def _hints(messages):
    return [
        part
        for part in _text_parts(messages)
        if isinstance(part, TextContent) and (part.metadata or {}).get("source_id") == "a13n-harness-ui.surface"
    ]


@pytest.mark.parametrize("surface", ["tui", "webui"])
def test_surface_hint_preserves_native_parts_and_uses_hidden_xml(surface):
    text = TextContent("Compare this", metadata={"source_id": "authored", "custom": {"index": 1}})
    image = BinaryContent(data=b"pixels", media_type="image/png")
    prompt = [text, image, " then explain"]
    before = deepcopy(prompt)

    result = append_surface_hint(prompt, surface)

    assert prompt == before
    assert result[:-1] == tuple(prompt)
    assert result[0] is text and result[1] is image
    hint = result[-1]
    assert isinstance(hint, TextContent)
    assert hint.metadata == {"display": False, "source_id": "a13n-harness-ui.surface"}
    element = ElementTree.fromstring(hint.content)
    assert element.tag == "surface-context"
    assert element.attrib == {"source": "a13n-harness-ui"}
    assert f"Harness UI {surface.upper() if surface == 'tui' else 'WebUI'}" in element.text
    assert "For the response to this input" in element.text
    if surface == "webui":
        assert "static diagrams" in element.text
        assert "interactive callbacks are not supported" in element.text
    else:
        assert "as source text rather than diagrams" in element.text
        assert "does not restrict formats written to files" in element.text
    assert append_surface_hint("hello", surface)[0] == "hello"


@pytest.mark.anyio
async def test_surface_hint_is_per_submission_hidden_and_not_externalized(tmp_path, monkeypatch):
    observed = []

    def model(messages, info):
        observed.append(deepcopy(messages))
        return ModelResponse(parts=[TextPart("Received")])

    async with _app(tmp_path, monkeypatch, _function_model(model), threshold=10) as (app, _, _):
        thread = await app.create_thread()
        for surface in (None, "webui", "tui"):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello", input_surface=surface)
            outcome = await app.wait_root_operation(receipt.receipt_id)
            assert outcome.status is RootOperationStatus.completed, outcome.failure

        assert _hints(observed[0]) == []
        assert len(_hints(observed[1])) == 1
        assert "Harness UI WebUI" in _hints(observed[1])[0].content
        assert len(_hints(observed[2])) == 2
        assert "Harness UI TUI" in _hints(observed[2])[-1].content
        assert not _references(observed[2])  # Even a threshold below hint length must leave it inline.

        page = await app.get_thread_transcript(thread_id=thread.thread_id)
        hints = [
            part
            for entry in page.entries
            for part in entry.parts
            if part.metadata.source_id == "a13n-harness-ui.surface"
        ]
        assert len(hints) == 2
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
