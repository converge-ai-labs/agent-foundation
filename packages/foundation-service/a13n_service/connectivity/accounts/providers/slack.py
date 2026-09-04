"""Slack Account send tools and channel scope."""

import httpx2
from pydantic import Field

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.native_actions import NativeAction, _credential, action
from a13n_service.connectivity.providers.slack.client import SlackNativeClient
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.ids import new_object_id

from ..domain import StrictModel
from .contracts import Provider, ProviderId

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
            bot_token=_credential(credentials, "bot_token"),
            request_id=new_object_id("message"),
        )

    selected = action(_SEND_MESSAGE, SlackSendArguments, send_slack)
    return {selected.definition.name: selected}


PROVIDER = Provider(SlackScope, frozenset({_SEND_MESSAGE}), actions)
