"""Slack Account send tools and channel scope."""

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import Field

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.native_actions import NativeAction, action, credential
from a13n_service.connectivity.providers.slack.client import SlackNativeClient
from a13n_service.ids import new_object_id

from ...accounts.domain import StrictModel
from ..tool_contracts import AccountTools, ProviderId

_SEND_MESSAGE = "slack.send_message"


class SlackScope(StrictModel):
    channel_ids: tuple[ProviderId, ...] = Field(max_length=128)


class SlackSendArguments(StrictModel):
    channel_id: ProviderId
    text: str = Field(min_length=1, max_length=40_000, repr=False)


def actions(
    configuration: JsonObject,
    credentials: JsonObject,
    target_scope: JsonObject,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> dict[str, NativeAction]:
    scope = SlackScope.model_validate(target_scope)
    client = SlackNativeClient(http)

    async def send_slack(arguments: SlackSendArguments):
        if arguments.channel_id not in scope.channel_ids:
            raise ValueError("account_target_not_authorized")
        return await client.send_message(
            arguments.channel_id,
            arguments.text,
            bot_token=credential(credentials, "bot_token"),
            request_id=new_object_id("message"),
        )

    selected = action(_SEND_MESSAGE, SlackSendArguments, send_slack, hide_receipt=True)
    return {selected.definition.name: selected}


ACCOUNT_TOOLS = AccountTools(SlackScope, frozenset({_SEND_MESSAGE}), actions)
