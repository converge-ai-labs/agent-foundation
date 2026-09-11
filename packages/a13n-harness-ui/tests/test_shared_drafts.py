from __future__ import annotations

import base64

import pytest
from a13n_harness_ui.shared_drafts import DraftCommand, SharedDraft, composer_document, composer_values
from pycrdt import Doc, Map, Text

pytestmark = pytest.mark.anyio


def replica(source: Doc) -> Doc:
    result = composer_document()
    result.apply_update(source.get_update())
    return result


def sync(draft: SharedDraft, document: Doc) -> DraftCommand:
    return DraftCommand(
        kind="sync", draft_id=draft.draft_id, update_base64=base64.b64encode(document.get_update()).decode()
    )


async def valid(_attachments: tuple[str, ...]) -> None:
    pass


@pytest.mark.parametrize("clear_first", [True, False])
async def test_client_capture_clear_merges_only_seen_items(clear_first: bool) -> None:
    """No server Send version: a client-owned clone clears CRDT item identities."""
    draft = SharedDraft()
    participant = draft.attach()
    author = composer_document()
    author.get("text", type=Text).insert(0, "submitted")
    author.get("attachments", type=Map[str])["selected"] = "old-capture"
    await draft.command(participant, sync(draft, author), valid)
    captured = replica(author)
    next_input = replica(author)
    next_input.get("text", type=Text).insert(3, "NEW")
    next_input.get("attachments", type=Map[str])["selected"] = "new-capture"
    next_input.get("attachments", type=Map[str])["another"] = "added"
    # The immutable input is read before ordinary HTTP submission. Only after
    # positive acknowledgment does this CLIENT perform its deletion transaction.
    assert composer_values(captured) == ("submitted", ("old-capture",))
    with captured.transaction():
        del captured.get("text", type=Text)[:]
        for key in list(captured.get("attachments", type=Map[str]).keys()):
            del captured.get("attachments", type=Map[str])[key]
    for incoming in (captured, next_input) if clear_first else (next_input, captured):
        await draft.command(participant, sync(draft, incoming), valid)
    assert composer_values(draft.document) == ("NEW", ("added", "new-capture"))
    for client in (author, captured, next_input):
        client.apply_update(draft.document.get_update())
        assert composer_values(client) == composer_values(draft.document)


async def test_offline_full_replica_updates_converge_and_invalid_selection_is_atomic() -> None:
    draft = SharedDraft()
    first, second = draft.attach(), draft.attach()
    a, b = replica(draft.document), replica(draft.document)
    a.get("text", type=Text).insert(0, "你好")
    b.get("text", type=Text).insert(0, "🌍")
    await draft.command(first, sync(draft, a), valid)
    await draft.command(second, sync(draft, b), valid)
    a.apply_update(draft.document.get_update())
    b.apply_update(draft.document.get_update())
    assert str(a["text"]) == str(b["text"]) and len(str(a["text"])) == 3
    before = draft.document.get_update()
    a.get("text", type=Text).insert(0, "not accepted")
    a.get("attachments", type=Map[str])["file"] = "foreign-thread-attachment"

    async def missing(ids: tuple[str, ...]) -> None:
        assert ids == ("foreign-thread-attachment",)
        raise ValueError("Attachment not found")

    with pytest.raises(ValueError, match="not found"):
        await draft.command(first, sync(draft, a), missing)
    assert draft.document.get_update() == before
    assert "not accepted" in str(a["text"])  # reject/unknown leaves local draft alone
    draft.detach(first)
    assert set(draft.frame(second).participants) == {second}
    draft.close()
    assert draft.frame(second).closed


@pytest.mark.parametrize("bad", [b"not a CRDT", b"\x01", b""])
async def test_malformed_updates_do_not_mutate_shared_state(bad: bytes) -> None:
    draft = SharedDraft()
    participant = draft.attach()
    with pytest.raises(ValueError):
        await draft.command(
            participant,
            DraftCommand(kind="sync", draft_id=draft.draft_id, update_base64=base64.b64encode(bad).decode()),
            valid,
        )
    assert composer_values(draft.document) == ("", ())
