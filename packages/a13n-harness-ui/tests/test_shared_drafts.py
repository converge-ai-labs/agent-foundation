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
    selected, extra = "inline-" + "1" * 36, "inline-" + "2" * 36
    author.get("attachments", type=Map[str])[selected] = "old-capture"
    author.get("text", type=Text).insert(len(str(author["text"])), f"\ufffc{selected}\ufffc")
    await draft.command(participant, sync(draft, author), valid)
    captured = replica(author)
    next_input = replica(author)
    next_input.get("text", type=Text).insert(3, "NEW")
    next_input.get("attachments", type=Map[str])[selected] = "new-capture"
    next_input.get("attachments", type=Map[str])[extra] = "added"
    next_input.get("text", type=Text).insert(0, f"\ufffc{extra}\ufffc\ufffc{selected}\ufffc")
    # The immutable input is read before ordinary HTTP submission. Only after
    # positive acknowledgment does this CLIENT perform its deletion transaction.
    assert composer_values(captured) == (f"submitted\ufffc{selected}\ufffc", ("old-capture",))
    with captured.transaction():
        del captured.get("text", type=Text)[:]
    for incoming in (captured, next_input) if clear_first else (next_input, captured):
        await draft.command(participant, sync(draft, incoming), valid)
    assert composer_values(draft.document) == (
        f"\ufffc{extra}\ufffc\ufffc{selected}\ufffcNEW",
        ("added", "new-capture"),
    )
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
    key = "inline-" + "f" * 36
    a.get("attachments", type=Map[str])[key] = "foreign-thread-attachment"
    a.get("text", type=Text).insert(0, f"\ufffc{key}\ufffc")

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


async def test_inline_registry_selects_live_order_and_bounds_pending_tokens() -> None:
    draft = SharedDraft()
    participant = draft.attach()
    author = composer_document()
    registry = author.get("attachments", type=Map[str])
    keys = [f"inline-{index:036x}" for index in range(12)]
    for index, key in enumerate(keys):
        registry[key] = f"attachment-{index}"
    registry[keys[1]] = "pending"
    author.get("text", type=Text).insert(0, f"before \ufffc{keys[2]}\ufffc middle \ufffc{keys[1]}\ufffc after")
    selected = []

    async def validate(ids: tuple[str, ...]) -> None:
        selected.append(ids)

    await draft.command(participant, sync(draft, author), validate)
    assert selected[-1] == ("attachment-2",)
    registry[keys[1]] = "attachment-1"
    await draft.command(participant, sync(draft, author), validate)
    assert selected[-1] == ("attachment-2", "attachment-1")
    with author.transaction():
        del author.get("text", type=Text)[:]
        author.get("text", type=Text).insert(0, "".join(f"\ufffc{key}\ufffc" for key in keys[:9]))
    before = draft.document.get_update()
    with pytest.raises(ValueError, match="eight"):
        await draft.command(participant, sync(draft, author), validate)
    assert draft.document.get_update() == before


async def test_editor_presence_expires_without_changing_draft_or_losing_names(monkeypatch):
    from a13n_harness_ui.shared_drafts import DraftPresence

    clock = 100.0
    monkeypatch.setattr("a13n_harness_ui.shared_drafts.monotonic", lambda: clock)
    draft = SharedDraft()
    first, observer = draft.attach(), draft.attach()
    author = composer_document()
    author.get("text", type=Text).insert(0, "Keep shared input")
    await draft.command(first, sync(draft, author), valid)
    before = draft.document.get_update()
    await draft.command(
        first,
        DraftCommand(
            kind="presence",
            draft_id=draft.draft_id,
            presence=DraftPresence(name="Alice", color="#2563eb", anchor="YQ==", head="Yg=="),
        ),
        valid,
    )
    changed = draft.changed
    clock += 29
    draft.expire_presence()
    assert not changed.is_set()
    assert draft.frame(observer).participants[first].head == "Yg=="
    clock += 1
    draft.expire_presence()
    assert changed.is_set()
    peer = draft.frame(observer).participants[first]
    assert peer.name == "Alice" and peer.color == "#2563eb"
    assert peer.anchor is None and peer.head is None
    assert draft.document.get_update() == before
    # Detached identities have no expiry that can later reintroduce them.
    draft.detach(first)
    clock += 30
    assert first not in draft.frame(observer).participants


async def test_unsent_membership_changes_only_after_validated_content_transitions() -> None:
    from a13n_harness_ui.shared_drafts import DraftPresence

    draft = SharedDraft()
    participant = draft.attach()
    author = composer_document()
    text = author.get("text", type=Text)
    registry = author.get("attachments", type=Map[str])
    text.insert(0, " \n")
    assert not await draft.command(participant, sync(draft, author), valid)
    assert draft.unsent_since is None
    key = "inline-00000000-0000-0000-0000-000000000000"
    registry[key] = "pending"
    # A dormant undo identity does not select input.
    assert not await draft.command(participant, sync(draft, author), valid)
    text.insert(0, f"\ufffc{key}\ufffc")
    assert await draft.command(participant, sync(draft, author), valid)
    since = draft.unsent_since
    assert since is not None
    registry[key] = "failed"
    text.insert(0, "Keep editing")
    assert not await draft.command(participant, sync(draft, author), valid)
    assert draft.unsent_since == since
    assert not await draft.command(
        participant, DraftCommand(kind="presence", draft_id=draft.draft_id, presence=DraftPresence(name="Peer")), valid
    )
    assert draft.unsent_since == since
    del text[:]

    async def reject(_attachments: tuple[str, ...]) -> None:
        raise ValueError("Rejected edit")

    with pytest.raises(ValueError, match="Rejected edit"):
        await draft.command(participant, sync(draft, author), reject)
    assert draft.unsent_since == since
    assert await draft.command(participant, sync(draft, author), valid)
    assert draft.unsent_since is None
    registry["legacy"] = "failed"
    assert not await draft.command(participant, sync(draft, author), valid)
    assert draft.unsent_since is None
    text.insert(0, f"\ufffc{key}\ufffc")
    assert await draft.command(participant, sync(draft, author), valid)
    assert draft.unsent_since is not None
    draft.detach(participant)
    assert draft.unsent_since is not None
