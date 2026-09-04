from __future__ import annotations

from datetime import UTC, datetime

from a13n_service.hooks.delivery import (
    DELIVERY_ID_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    DeliveryEnvelope,
    signed_request_headers,
)


def envelope() -> DeliveryEnvelope:
    return DeliveryEnvelope(
        delivery_id="dlv_1234567890abcdef",
        hook_subscription_id="hsub_1234567890abcdef",
        hook_name="run.completed",
        hook_schema_version="1",
        source_id="lev_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        resource_type="run",
        resource_id="run_1234567890abcdef",
        resource_seq=4,
        resource_version=7,
        session_id="sess_1234567890abcdef",
        thread_id=None,
        run_id="run_1234567890abcdef",
        occurred_at=datetime(2026, 9, 3, 1, 2, 3, tzinfo=UTC),
        payload={"status": "completed", "count": 2},
    )


def test_delivery_envelope_has_stable_canonical_bytes_and_signature() -> None:
    delivery = envelope()

    assert delivery.canonical_bytes() == (
        b'{"delivery_id":"dlv_1234567890abcdef","hook_name":"run.completed",'
        b'"hook_schema_version":"1","hook_subscription_id":"hsub_1234567890abcdef",'
        b'"occurred_at":"2026-09-03T01:02:03Z","payload":{"count":2,"status":"completed"},'
        b'"resource_id":"run_1234567890abcdef","resource_seq":4,"resource_type":"run",'
        b'"resource_version":7,"run_id":"run_1234567890abcdef",'
        b'"session_id":"sess_1234567890abcdef","source_id":"lev_1234567890abcdef",'
        b'"source_kind":"lifecycle_event","workspace_id":"ws_1234567890abcdef"}'
    )
    headers = signed_request_headers(
        delivery,
        signing_secret="test-signing-secret",
        signed_at=datetime(2026, 9, 3, 1, 2, 4, 999_999, tzinfo=UTC),
    )

    assert headers == {
        "Content-Type": "application/json",
        DELIVERY_ID_HEADER: "dlv_1234567890abcdef",
        TIMESTAMP_HEADER: "1788397324",
        SIGNATURE_HEADER: "v1=81dd2f022fb5fbac16ee1cd08a86522773c516960aaf296999a6c935871e4622",
    }


def test_delivery_omits_unowned_optional_correlation() -> None:
    body = envelope().canonical_bytes()

    assert b'"thread_id"' not in body
    assert b'"run_attempt_id"' not in body
    assert b'"harness_run_id"' not in body
