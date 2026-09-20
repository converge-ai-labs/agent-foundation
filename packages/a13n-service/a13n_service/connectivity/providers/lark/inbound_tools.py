"""Lark inbound tools bound to the admitted conversation."""

from functools import partial

from a13n_service.ids import new_object_id

from ...native_actions import CONVERSATION_REPLY_DESCRIPTION, NativeAction, action, credential
from ..definition import InboundActionContext
from ..lark.actions import (
    LarkActionBinding,
    LarkAutoReplyArguments,
    LarkForcedReplyArguments,
    LarkListMembersArguments,
    LarkReadMessagesArguments,
)
from ..lark.adapter import LarkAccountConfig
from ..lark.client import LarkNativeClient
from ..lark.token import LarkTenantTokenProvider


def inbound_actions(inbound: InboundActionContext) -> dict[str, NativeAction]:
    context, policy = inbound.provider_context, inbound.action_policy
    configuration, credentials = inbound.configuration, inbound.credentials
    http, endpoints = inbound.http, inbound.endpoints
    config = LarkAccountConfig.model_validate(configuration)
    binding = LarkActionBinding.model_validate(
        {
            **{key: context.get(key) for key in ("chat_id", "message_id", "discussion_id", "chat_type")},
            "reply_mode": policy.get("reply_mode", "thread"),
        }
    )
    tokens = LarkTenantTokenProvider(
        http,
        endpoints,
        open_api_origin=config.open_api_origin,
        app_id=config.app_id,
        app_secret=credential(credentials, "app_secret"),
    )
    client = LarkNativeClient(http, endpoints, tokens, open_api_origin=config.open_api_origin)

    async def reply(arguments):
        return await client.reply(
            binding, arguments, request_id=new_object_id("reply"), effect_id=new_object_id("effect")
        )

    actions = (
        action(
            "lark.reply", LarkAutoReplyArguments, reply, hide_receipt=True, description=CONVERSATION_REPLY_DESCRIPTION
        )
        if binding.reply_mode == "auto"
        else action(
            "lark.reply", LarkForcedReplyArguments, reply, hide_receipt=True, description=CONVERSATION_REPLY_DESCRIPTION
        ),
        action("lark.list_members", LarkListMembersArguments, partial(client.list_members, binding)),
        action("lark.read_messages", LarkReadMessagesArguments, partial(client.read_messages, binding)),
    )
    return {item.definition.name: item for item in actions}
