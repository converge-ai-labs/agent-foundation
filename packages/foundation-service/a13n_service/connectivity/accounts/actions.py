"""Proactive operations with explicit provider target authorization."""

from functools import partial
from typing import Literal

import httpx2
from pydantic import Field

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.native_actions import NativeAction, _credential, action, native_actions
from a13n_service.connectivity.providers.github.actions import (
    GitHubAddCommentArguments,
    GitHubListPrFilesArguments,
    GitHubReadCommentsArguments,
    GitHubReadTargetArguments,
)
from a13n_service.connectivity.providers.lark.actions import LarkReplyContent
from a13n_service.connectivity.providers.lark.adapter import LarkAccountConfig
from a13n_service.connectivity.providers.lark.client import LarkNativeClient
from a13n_service.connectivity.providers.lark.token import LarkTenantTokenProvider
from a13n_service.connectivity.providers.slack.client import SlackNativeClient
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.ids import new_object_id

from .targets import GitHubScope, LarkScope, ProviderId, SlackScope, StrictModel


class SlackSendArguments(StrictModel):
    channel_id: ProviderId
    text: str = Field(min_length=1, max_length=40_000, repr=False)


class LarkSendArguments(StrictModel):
    chat_id: ProviderId
    content: LarkReplyContent


class GitHubTarget(StrictModel):
    repository_id: int = Field(gt=0)
    number: int = Field(gt=0)
    target_kind: Literal["issue", "pull_request"]


class GitHubCommentArguments(GitHubTarget, GitHubAddCommentArguments):
    pass


class GitHubCommentsArguments(GitHubTarget, GitHubReadCommentsArguments):
    pass


class GitHubReadArguments(GitHubTarget, GitHubReadTargetArguments):
    pass


class GitHubFilesArguments(GitHubTarget, GitHubListPrFilesArguments):
    pass


def account_actions(
    provider: str,
    configuration: JsonObject,
    credentials: JsonObject,
    target_scope: JsonObject,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> dict[str, NativeAction]:
    if provider == "slack":
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

        selected = action("slack.send_message", SlackSendArguments, send_slack)
        return {selected.definition.name: selected}
    if provider == "lark":
        lark_scope = LarkScope.model_validate(target_scope)
        config = LarkAccountConfig.model_validate(configuration)
        tokens = LarkTenantTokenProvider(
            http,
            endpoints,
            open_api_origin=config.open_api_origin,
            app_id=config.app_id,
            app_secret=_credential(credentials, "app_secret"),
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

        selected = action("lark.send_message", LarkSendArguments, send_lark)
        return {selected.definition.name: selected}
    if provider == "github":
        github_scope = GitHubScope.model_validate(target_scope)

        async def invoke(name: str, model: type[GitHubTarget], arguments: JsonObject):
            arguments = model.model_validate(arguments).model_dump(mode="json")
            target = GitHubTarget.model_validate(
                {key: arguments[key] for key in ("repository_id", "number", "target_kind")}
            )
            repository = next(
                (item for item in github_scope.repositories if item.repository_id == target.repository_id), None
            )
            if repository is None:
                raise ValueError("account_target_not_authorized")
            context: JsonObject = {
                "repository_id": repository.repository_id,
                "repository_owner": repository.owner,
                "repository_name": repository.repository,
                "number": target.number,
                "target_kind": target.target_kind,
            }
            actions = native_actions("github", context, {}, configuration, credentials, http, endpoints)
            selected = actions.get(name)
            if selected is None:
                raise ValueError("account_action_unavailable")
            return await selected.call(
                {key: value for key, value in arguments.items() if key not in GitHubTarget.model_fields}
            )

        from mcp.types import Tool

        return {
            name: NativeAction(
                Tool(name=name, description=name.replace(".", " "), inputSchema=model.model_json_schema()),
                partial(invoke, name, model),
            )
            for name, model in (
                ("github.add_comment", GitHubCommentArguments),
                ("github.read_comments", GitHubCommentsArguments),
                ("github.read_issue_or_pr", GitHubReadArguments),
                ("github.list_pr_files", GitHubFilesArguments),
            )
        }
    raise ValueError("account_provider_unavailable")
