"""Interactive task cards; task authority stays in the Bot application layer."""

import json
from functools import cache
from importlib.resources import files
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx2
from a13n_harness.http import EndpointValidator, ProviderHttpError
from pydantic import JsonValue

from a13n_service.connectivity.domain import JsonObject

from .api import read_lark_response
from .token import LarkTenantTokenProvider

_LABELS = {
    "accepted": ("Queued", "The task has been accepted and is waiting to run.", "blue"),
    "running": ("Working", "The task is running.", "blue"),
    "waiting": (
        "Action required",
        "The task is waiting for input, approval, or an external result. Open details to continue.",
        "orange",
    ),
    "stopping": ("Stopping", "A stop request has been received. Checking execution status.", "orange"),
    "completed": (
        "Execution completed",
        "Execution has completed. This does not confirm that the result was delivered.",
        "green",
    ),
    "failed": ("Execution failed", "The task could not finish. Open details for more information.", "red"),
    "cancelled": ("Stopped", "The task was cancelled. Completed external actions are not undone.", "grey"),
}


@cache
def _translations() -> dict[str, object]:
    return json.loads(files(__package__).joinpath("progress_zh_cn.json").read_text(encoding="utf-8"))


def action_toast(message: str, kind: str) -> JsonObject:
    translations = _translations().get("toasts")
    chinese = translations.get(message) if isinstance(translations, dict) else None
    toast: JsonObject = {"type": kind, "content": message}
    if isinstance(chinese, str):
        toast["i18n"] = {"en_us": message, "zh_cn": chinese}
    return {"toast": toast}


def task_card(
    *,
    status: str,
    run_id: str,
    token: str,
    details_url: str | None,
    language: str = "en_us",
    replies: tuple[str, ...] = (),
) -> JsonObject:
    title, description, color = _LABELS[status]
    texts = _translations() if language == "zh_cn" else {}
    localized = texts.get(status)
    if isinstance(localized, dict):
        title, description = str(localized["title"]), str(localized["description"])
    if replies and status == "completed":
        title = str(texts.get("answered_title", "Completed"))
        description = str(texts.get("answered_description", "The task has completed."))
    actions: list[JsonValue] = []
    if status in {"accepted", "running"}:
        actions.append(
            {
                "tag": "button",
                "type": "default",
                "text": {"tag": "plain_text", "content": str(texts.get("stop", "Stop task"))},
                "value": {"kind": "a13n.task_control.v1", "action": "stop", "run_id": run_id, "token": token},
                "confirm": {
                    "title": {"tag": "plain_text", "content": str(texts.get("confirm_title", "Stop this task?"))},
                    "text": {
                        "tag": "plain_text",
                        "content": str(
                            texts.get(
                                "confirm_body",
                                "Only the requester can stop this task. Completed actions will not be undone.",
                            )
                        ),
                    },
                },
            }
        )
    if details_url:
        actions.append(
            {
                "tag": "button",
                "type": "default",
                "text": {"tag": "plain_text", "content": str(texts.get("details", "Open details"))},
                "url": details_url,
            }
        )
    elements: list[JsonValue] = []
    for reply in replies:
        if elements:
            elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": reply})
    if replies:
        elements.append({"tag": "hr"})
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": description}]})
    if actions:
        elements.append({"tag": "action", "actions": actions})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True, "enable_forward": False},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
        "elements": elements,
    }


def validate_card(card: JsonObject, *, reserve_bytes: int = 0) -> None:
    # Bound the double-encoded request body, not only the visible text. Never truncate an answer.
    payload = {"content": json.dumps(card, ensure_ascii=False)}
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + reserve_bytes > 28_000:
        raise ProviderHttpError("invalid_arguments")
    elements = card.get("elements")
    if isinstance(elements, list) and len(elements) > 45:
        raise ProviderHttpError("invalid_arguments")


class LarkProgressClient:
    def __init__(
        self, http: httpx2.AsyncClient, endpoints: EndpointValidator, *, origin: str, app_id: str, secret: str
    ) -> None:
        self.http = http
        self.endpoints = endpoints
        self.origin = origin
        self.tokens = LarkTenantTokenProvider(http, endpoints, open_api_origin=origin, app_id=app_id, app_secret=secret)

    async def publish(
        self,
        *,
        source_message_id: str,
        conversation_id: str,
        reply_in_thread: bool,
        message_id: str | None,
        run_id: str,
        card: JsonObject,
    ) -> str:
        validate_card(card)
        try:
            origin = await self.endpoints.validate(self.origin, resolve_dns=True)
        except ValueError as error:
            raise ProviderHttpError("endpoint_denied") from error
        token = await self.tokens.token()
        body: JsonObject = {"content": json.dumps(card, ensure_ascii=False)}
        params = None
        if message_id is None:
            if reply_in_thread:
                path = f"/open-apis/im/v1/messages/{quote(source_message_id, safe='')}/reply"
                body["reply_in_thread"] = True
            else:
                path = "/open-apis/im/v1/messages"
                body["receive_id"] = conversation_id
                params = {"receive_id_type": "chat_id"}
            body.update({"msg_type": "interactive", "uuid": str(uuid5(NAMESPACE_URL, f"a13n:progress:{run_id}"))})
            method = "POST"
        else:
            path = f"/open-apis/im/v1/messages/{quote(message_id, safe='')}"
            method = "PATCH"
        async with self.http.stream(
            method,
            origin + path,
            json=body,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        ) as response:
            value = await read_lark_response(response, max_bytes=64 * 1024)
        if message_id is not None:
            return message_id
        data = value.get("data")
        identifier = data.get("message_id") if isinstance(data, dict) else None
        if not isinstance(identifier, str) or not 0 < len(identifier) <= 256:
            raise ProviderHttpError("invalid_provider_response")
        return identifier
