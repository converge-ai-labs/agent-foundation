"""Routine callbacks use authenticated, distinct action namespaces."""

import pytest


def test_lark_rejects_cross_namespace_or_forged_routine_actions():
    from a13n_service.connectivity.ingress.provider import ProviderRequestError
    from a13n_service.connectivity.providers.lark.wire import LarkIdentity, authenticate_and_normalize

    from .test_lark import _ENCRYPT_KEY, _VERIFICATION_TOKEN, NOW, _encrypted_request, _payload

    payload = _payload()
    payload["header"]["event_type"] = "card.action.trigger"
    payload["event"] = {
        "host": "im_message",
        "operator": {"open_id": "ou_owner"},
        "context": {"open_chat_id": "oc_chat", "open_message_id": "om_card"},
        "action": {
            "value": {
                "kind": "a13n.task_control.v1",
                "action": "routine_confirm",
                "run_id": "routine_test",
                "token": "token",
            }
        },
    }
    kwargs = dict(
        identity=LarkIdentity("cli_app", "tenant-1", "ou_bot"),
        encrypt_key=_ENCRYPT_KEY,
        verification_token=_VERIFICATION_TOKEN,
        received_at=NOW,
    )
    with pytest.raises(ProviderRequestError):
        authenticate_and_normalize(_encrypted_request(payload), **kwargs)
    payload["event"]["action"]["value"].update(kind="a13n.routine.v1", action="confirm", routine_id="routine_test")
    with pytest.raises(ProviderRequestError):
        authenticate_and_normalize(_encrypted_request(payload, signature="forged"), **kwargs)
