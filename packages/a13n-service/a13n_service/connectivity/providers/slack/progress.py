"""Slack Block Kit task messages and updates through the existing Web API client."""

import json

import httpx2
from a13n_harness.http import ProviderHttpError
from pydantic import JsonValue

from a13n_service.connectivity.domain import JsonObject

from .client import SlackNativeClient

STOP_ACTION_ID = "a13n.task_control.v1.stop"
_LABELS = {
    "accepted": ("Queued", "The task has been accepted and is waiting to run."),
    "running": ("Working", "The task is running."),
    "stopping": ("Stopping", "A stop request has been received. Checking execution status."),
    "waiting": ("Action required", "The task needs input, approval, or an external result. Open details to continue."),
    "completed": (
        "Execution completed",
        "Execution has completed. This does not confirm that the result was delivered.",
    ),
    "failed": ("Execution failed", "The task could not finish. Open details for more information."),
    "cancelled": ("Stopped", "The task was cancelled. Completed external actions are not undone."),
}


def task_message(
    *, status: str, run_id: str, token: str, details_url: str | None, replies: tuple[str, ...] = ()
) -> JsonObject:
    title, description = _LABELS[status]
    if replies and status == "completed":
        title, description = "Completed", "The task has completed."
    # Reject excessive content before dispatch; do not silently truncate an explicit answer.
    if sum(map(len, replies)) > 32_000:
        raise ProviderHttpError("invalid_arguments")
    blocks: list[JsonValue] = [{"type": "header", "text": {"type": "plain_text", "text": title}}]
    for reply in replies:
        if len(blocks) > 1:
            blocks.append({"type": "divider"})
        for offset in range(0, len(reply), 3000):
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": reply[offset : offset + 3000], "verbatim": True}}
            )
    blocks.append({"type": "context", "elements": [{"type": "plain_text", "text": description}]})
    actions: list[JsonValue] = []
    if status in {"accepted", "running"}:
        actions.append(
            {
                "type": "button",
                "action_id": STOP_ACTION_ID,
                "text": {"type": "plain_text", "text": "Stop task"},
                "value": json.dumps({"run_id": run_id, "token": token}, separators=(",", ":")),
                "confirm": {
                    "title": {"type": "plain_text", "text": "Stop this task?"},
                    "text": {
                        "type": "plain_text",
                        "text": "Only the requester can stop this task. Completed actions will not be undone.",
                    },
                    "confirm": {"type": "plain_text", "text": "Stop task"},
                    "deny": {"type": "plain_text", "text": "Keep working"},
                },
            }
        )
    if details_url:
        actions.append(
            {
                "type": "button",
                "action_id": "a13n.task_details.v1",
                "text": {"type": "plain_text", "text": "Open details"},
                "url": details_url,
            }
        )
    if actions:
        blocks.append({"type": "actions", "elements": actions})
    if len(blocks) > 48:
        raise ProviderHttpError("invalid_arguments")
    # Full fallback content is accessible to screen readers, not just the status title.
    fallback = "\n\n".join((title, *replies, description, *((details_url,) if details_url else ())))
    return {"text": fallback, "blocks": blocks}


class SlackProgressClient:
    def __init__(self, http: httpx2.AsyncClient, *, token: str) -> None:
        self.client = SlackNativeClient(http)
        self.token = token

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
        del run_id
        payload: JsonObject = {**card, "channel": conversation_id}
        if message_id is not None:
            payload["ts"] = message_id
        else:
            payload.update({"unfurl_links": False, "unfurl_media": False})
            if reply_in_thread:
                payload["thread_ts"] = source_message_id
        return await self.client.publish_progress(payload, bot_token=self.token, message_id=message_id)
