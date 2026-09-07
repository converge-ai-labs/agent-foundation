"""Lark Account send tools and chat scope."""

import httpx2
from pydantic import Field

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.native_actions import NativeAction, action, credential
from a13n_service.connectivity.providers.lark.actions import LarkReplyContent
from a13n_service.connectivity.providers.lark.adapter import LarkAccountConfig
from a13n_service.connectivity.providers.lark.client import LarkNativeClient
from a13n_service.connectivity.providers.lark.token import LarkTenantTokenProvider
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.ids import new_object_id

from ...accounts.domain import StrictModel
from ..tool_contracts import AccountTools, ProviderId

_SEND_MESSAGE = "lark.send_message"


class LarkScope(StrictModel):
    chat_ids: tuple[ProviderId, ...] = Field(max_length=128)


class LarkSendArguments(StrictModel):
    chat_id: ProviderId
    content: LarkReplyContent


def actions(
    configuration: JsonObject,
    credentials: JsonObject,
    target_scope: JsonObject,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> dict[str, NativeAction]:
    lark_scope = LarkScope.model_validate(target_scope)
    config = LarkAccountConfig.model_validate(configuration)
    tokens = LarkTenantTokenProvider(
        http,
        endpoints,
        open_api_origin=config.open_api_origin,
        app_id=config.app_id,
        app_secret=credential(credentials, "app_secret"),
    )
    lark = LarkNativeClient(http, endpoints, tokens, open_api_origin=config.open_api_origin)

    async def send_lark(arguments: LarkSendArguments):
        if arguments.chat_id not in lark_scope.chat_ids:
            raise ValueError("account_target_not_authorized")
        return await lark.send_message(
            arguments.chat_id,
            arguments.content,
            effect_id=new_object_id("effect"),
            request_id=new_object_id("message"),
        )

    selected = action(_SEND_MESSAGE, LarkSendArguments, send_lark, hide_receipt=True)
    return {selected.definition.name: selected}


ACCOUNT_TOOLS = AccountTools(LarkScope, frozenset({_SEND_MESSAGE}), actions)
