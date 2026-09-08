"""Trusted inbox provenance carried by native, model-invisible input metadata."""

from collections.abc import Iterable, Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace

from a13n_harness import RunInputValue
from a13n_harness.errors import RunError
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelRequest, TextContent, UserContent, UserPromptPart

from .state import ConsumedThreadInboxEntry

_PROVENANCE_KEY = "a13n.service.inbox"
_CONTENT = TypeAdapter(tuple[UserContent, ...])


@dataclass(frozen=True, slots=True)
class AdaptedThreadInboxEntry:
    """One authorized FIFO entry materialized for native Harness enqueue."""

    delivery_sequence: int
    receipt: ConsumedThreadInboxEntry
    input: RunInputValue

    def __post_init__(self) -> None:
        if self.delivery_sequence < 1:
            raise ValueError("Thread inbox delivery sequence must be positive")

    def tagged_input(self, run_id: str) -> tuple[UserContent, ...]:
        content: list[UserContent] = list(deepcopy((self.input,) if isinstance(self.input, str) else self.input))
        if not content:
            raise RunError("Thread inbox input is empty.", code="service_inbox_input_invalid")
        # The adapter cannot manufacture another entry's trusted provenance.
        for index, item in enumerate(content):
            if isinstance(item, TextContent) and isinstance(item.metadata, dict):
                content[index] = replace(
                    item, metadata={k: v for k, v in item.metadata.items() if k != _PROVENANCE_KEY}
                )
        if isinstance(content[0], str):
            content[0] = TextContent(content[0])
        if not isinstance(content[0], TextContent) or (
            content[0].metadata is not None and not isinstance(content[0].metadata, dict)
        ):
            # Binary-only input still needs application metadata, never vendor metadata.
            content.insert(0, TextContent(""))
        first = content[0]
        assert isinstance(first, TextContent)
        content[0] = replace(
            first,
            metadata={
                **(first.metadata or {}),
                _PROVENANCE_KEY: {
                    "run_id": run_id,
                    "inbox_entry_id": self.receipt.inbox_entry_id,
                    "kind": self.receipt.kind,
                },
            },
        )
        return tuple(content)


def incorporated_receipts(
    messages: Sequence[ModelMessage],
    entries: Iterable[AdaptedThreadInboxEntry],
    *,
    run_id: str,
) -> tuple[ConsumedThreadInboxEntry, ...]:
    """Match complete native content and trusted identity, never text alone."""

    expected = {entry.receipt.inbox_entry_id: entry for entry in entries}
    found: set[str] = set()
    for provenance, content in _retained_provenance(messages, run_id=run_id):
        entry_id = provenance.get("inbox_entry_id")
        if not isinstance(entry_id, str) or (entry := expected.get(entry_id)) is None:
            continue
        if provenance.get("kind") != entry.receipt.kind:
            raise RunError("Thread inbox provenance kind conflicts.", code="service_inbox_receipt_invalid")
        # Native serialization narrows BinaryContent to media-specific types.
        if _CONTENT.dump_python(tuple(content), mode="json") == _CONTENT.dump_python(
            entry.tagged_input(run_id), mode="json"
        ):
            found.add(entry_id)
    return tuple(entry.receipt for entry_id, entry in expected.items() if entry_id in found)


def retained_inbox_ids(messages: Sequence[ModelMessage], *, run_id: str) -> frozenset[str]:
    """Locate provenance for recovery inspection; identity alone never proves consumption."""

    return frozenset(
        entry_id
        for provenance, _ in _retained_provenance(messages, run_id=run_id)
        if isinstance(entry_id := provenance.get("inbox_entry_id"), str)
    )


def _retained_provenance(
    messages: Sequence[ModelMessage], *, run_id: str
) -> Iterator[tuple[dict, Sequence[UserContent]]]:
    for message in messages:
        if not isinstance(message, ModelRequest) or message.state != "complete":
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart) or isinstance(part.content, str) or not part.content:
                continue
            first = part.content[0]
            if not isinstance(first, TextContent) or not isinstance(first.metadata, dict):
                continue
            provenance = first.metadata.get(_PROVENANCE_KEY)
            if not isinstance(provenance, dict) or provenance.get("run_id") != run_id:
                continue
            yield provenance, part.content


def merge_receipts(
    prior: Iterable[ConsumedThreadInboxEntry],
    incorporated: Iterable[ConsumedThreadInboxEntry],
) -> tuple[ConsumedThreadInboxEntry, ...]:
    """Preserve FIFO order while merging each inbox identity exactly once."""

    receipts = {receipt.inbox_entry_id: receipt for receipt in prior}
    for receipt in incorporated:
        existing = receipts.setdefault(receipt.inbox_entry_id, receipt)
        if existing.kind != receipt.kind:
            raise RunError("Thread inbox receipt kind conflicts.", code="service_inbox_receipt_invalid")
    return tuple(receipts.values())
