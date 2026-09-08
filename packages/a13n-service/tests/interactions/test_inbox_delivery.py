from dataclasses import replace

import pytest
from a13n_harness import HarnessState
from a13n_harness.errors import RunError
from a13n_service.interactions.inbox_delivery import AdaptedThreadInboxEntry, incorporated_receipts, merge_receipts
from a13n_service.interactions.state import InboxReceipt
from pydantic_ai.messages import BinaryContent, ModelRequest, TextContent, UserPromptPart

from .conftest import RUN_ID


def _entry(input="same input", *, entry_id="inb_1234567890abcdef"):
    return AdaptedThreadInboxEntry(1, InboxReceipt(inbox_entry_id=entry_id, kind="steer"), input)


@pytest.mark.parametrize(
    "input",
    [
        "text",
        ["image", BinaryContent(b"image-bytes", media_type="image/png")],
        [BinaryContent(b"pdf", media_type="application/pdf")],
    ],
)
def test_inbox_identity_and_complete_input_survive_native_state_roundtrip(input):
    entry = _entry(input)
    state = HarnessState.new(message_history=[ModelRequest(parts=[UserPromptPart(entry.tagged_input(RUN_ID))])])
    restored = HarnessState.model_validate_json(state.model_dump_json())
    assert incorporated_receipts(restored.message_history, [entry], run_id=RUN_ID) == (entry.receipt,)
    assert incorporated_receipts(restored.message_history, [entry], run_id="run_another") == ()


def test_incomplete_binary_input_and_identical_unmarked_text_do_not_prove_incorporation():
    entry = _entry(["image", BinaryContent(b"image-bytes", media_type="image/png")])
    incomplete = ModelRequest(parts=[UserPromptPart(entry.tagged_input(RUN_ID)[:1])])
    unmarked = ModelRequest(parts=[UserPromptPart(entry.input)])
    assert incorporated_receipts([incomplete, unmarked], [entry], run_id=RUN_ID) == ()


def test_distinct_inbox_identities_remain_distinct_when_content_matches():
    first = _entry()
    second = _entry(entry_id="inb_fedcba0987654321")
    first_message = ModelRequest(parts=[UserPromptPart(first.tagged_input(RUN_ID))])
    second_message = ModelRequest(parts=[UserPromptPart(second.tagged_input(RUN_ID))])
    assert incorporated_receipts([first_message, first_message], [first, second], run_id=RUN_ID) == (first.receipt,)
    assert incorporated_receipts([second_message, first_message], [first, second], run_id=RUN_ID) == (
        first.receipt,
        second.receipt,
    )


def test_adapter_cannot_supply_another_inbox_identity():
    first = _entry()
    spoofed = replace(_entry(entry_id="inb_fedcba0987654321"), input=first.tagged_input(RUN_ID))
    tagged = spoofed.tagged_input(RUN_ID)
    assert isinstance(tagged[0], TextContent)
    messages = [ModelRequest(parts=[UserPromptPart(tagged)])]
    assert incorporated_receipts(messages, [first, spoofed], run_id=RUN_ID) == (spoofed.receipt,)


def test_receipts_deduplicate_by_identity_and_reject_conflicting_kinds():
    first = _entry().receipt
    second = _entry(entry_id="inb_fedcba0987654321").receipt
    assert merge_receipts([first], [first, second, second]) == (first, second)
    with pytest.raises(RunError, match="kind conflicts"):
        merge_receipts([first], [first.model_copy(update={"kind": "async_result"})])
