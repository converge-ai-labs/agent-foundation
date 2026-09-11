from datetime import UTC, datetime, timedelta, timezone

import pytest
from a13n_service.collection_cursors import encode_collection_cursor
from a13n_service.hooks.cursors import HookCursorError, decode_hook_cursor, encode_hook_cursor

SCOPE = {"workspace_id": "ws_1234567890abcdef", "filter": "通知"}
SUBSCRIPTION_ID = "hsub_1234567890abcdef"
# Issued by the original Hook cursor encoder, before sharing the envelope codec.
LEGACY_CURSOR = (
    "eyJpZCI6ImhzdWJfMTIzNDU2Nzg5MGFiY2RlZiIsInNjb3BlIjoiMzUwZDJiYzU1Y2QwNmNjY2Rj"
    "NDcwMjNmNDk3YjcyYzZlYmNkMTliNWQyMjc1YzdmYTdjNDJmODk5MmYxNDZjMSIsInVwZGF0ZWRf"
    "YXQiOiIyMDI2LTA5LTA1VDAwOjAwOjAwWiIsInYiOiIxIn0"
)


@pytest.mark.parametrize(
    "updated_at",
    [
        datetime(2026, 9, 5),
        datetime(2026, 9, 5, tzinfo=UTC),
        datetime(2026, 9, 5, 8, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_hook_cursor_preserves_issued_format_and_normalizes_time(updated_at: datetime) -> None:
    assert encode_hook_cursor(updated_at=updated_at, subscription_id=SUBSCRIPTION_ID, scope=SCOPE) == LEGACY_CURSOR
    assert decode_hook_cursor(LEGACY_CURSOR, scope=SCOPE) == (datetime(2026, 9, 5, tzinfo=UTC), SUBSCRIPTION_ID)


def test_hook_cursor_rejects_another_query() -> None:
    with pytest.raises(HookCursorError, match="cursor does not match this collection"):
        decode_hook_cursor(LEGACY_CURSOR, scope={**SCOPE, "filter": "changed"})


@pytest.mark.parametrize("cursor", ["", "!invalid!", pytest.param("a" * 2049, id="oversized-cursor"), "ew"])
def test_hook_cursor_rejects_invalid_envelopes(cursor: str) -> None:
    with pytest.raises(HookCursorError, match="invalid cursor"):
        decode_hook_cursor(cursor, scope=SCOPE)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": SUBSCRIPTION_ID},
        {"updated_at": "2026-09-05T00:00:00Z"},
        {"id": 123, "updated_at": "2026-09-05T00:00:00Z"},
        {"id": "ap_1234567890abcdef", "updated_at": "2026-09-05T00:00:00Z"},
        {"id": SUBSCRIPTION_ID, "updated_at": "invalid"},
    ],
)
def test_hook_cursor_retains_feature_payload_validation(payload: dict[str, object]) -> None:
    cursor = encode_collection_cursor(payload, scope=SCOPE)
    with pytest.raises(HookCursorError, match="invalid cursor"):
        decode_hook_cursor(cursor, scope=SCOPE)
