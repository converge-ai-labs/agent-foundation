"""Slack inbound tools bound to the admitted conversation."""

from functools import partial

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy

from a13n_service.ids import new_object_id

from ...domain import JsonObject
from ...native_actions import NativeAction, action, credential
from ..slack.client import (
    SlackActionBinding,
    SlackAutoReplyArguments,
    SlackForcedReplyArguments,
    SlackListMembersArguments,
    SlackNativeClient,
    SlackReadMessagesArguments,
)


def inbound_actions(
    context: JsonObject,
    policy: JsonObject,
    configuration: JsonObject,
    credentials: JsonObject,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> dict[str, NativeAction]:
    binding = SlackActionBinding.model_validate(
        {
            "channel_id": context.get("channel_id"),
            "root_thread_ts": context.get("root_thread_ts"),
            "conversation_kind": context.get("conversation_kind"),
            "reply_mode": policy.get("reply_mode", "thread"),
        }
    )
    client = SlackNativeClient(http)
    token = credential(credentials, "bot_token")

    async def reply(arguments):
        return await client.reply(binding, arguments, bot_token=token, request_id=new_object_id("reply"))

    actions = (
        action("slack.reply", SlackAutoReplyArguments, reply, hide_receipt=True)
        if binding.reply_mode == "auto"
        else action("slack.reply", SlackForcedReplyArguments, reply, hide_receipt=True),
        action("slack.list_members", SlackListMembersArguments, partial(client.list_members, binding, bot_token=token)),
        action(
            "slack.read_messages", SlackReadMessagesArguments, partial(client.read_messages, binding, bot_token=token)
        ),
    )
    return {item.definition.name: item for item in actions}
