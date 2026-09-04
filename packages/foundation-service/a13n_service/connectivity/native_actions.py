"""Provider-specific native tool schemas and typed calls."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial

import httpx2
from mcp.types import Tool
from pydantic import BaseModel, JsonValue

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.ids import new_object_id

from .domain import JsonObject
from .providers.github.actions import (
    GitHubActionBinding,
    GitHubAddCommentArguments,
    GitHubAddCommentSucceeded,
    GitHubListPrFilesArguments,
    GitHubReadCommentsArguments,
    GitHubReadTargetArguments,
)
from .providers.github.adapter import GitHubAccountConfig
from .providers.github.client import GitHubNativeClient
from .providers.github.token import GitHubInstallationTokenProvider
from .providers.lark.actions import (
    LarkActionBinding,
    LarkAutoReplyArguments,
    LarkForcedReplyArguments,
    LarkListMembersArguments,
    LarkReadMessagesArguments,
    LarkReplySucceeded,
)
from .providers.lark.adapter import LarkAccountConfig
from .providers.lark.client import LarkNativeClient
from .providers.lark.token import LarkTenantTokenProvider
from .providers.slack.client import (
    SlackActionBinding,
    SlackAutoReplyArguments,
    SlackForcedReplyArguments,
    SlackListMembersArguments,
    SlackNativeClient,
    SlackReadMessagesArguments,
    SlackReplySucceeded,
)


@dataclass(frozen=True, slots=True)
class NativeAction:
    definition: Tool
    call: Callable[[JsonObject], Awaitable[JsonValue]]


def action[Arguments: BaseModel](
    name: str, model: type[Arguments], call: Callable[[Arguments], Awaitable[BaseModel]]
) -> NativeAction:
    async def invoke(arguments: JsonObject) -> JsonValue:
        result = await call(model.model_validate(arguments))
        value = result.model_dump(mode="json")
        # Current-context replies preserve the admitted binding. Receipts are typed provider evidence,
        # never model-authored authority and never instructions to replace the target.
        if isinstance(result, (SlackReplySucceeded, LarkReplySucceeded, GitHubAddCommentSucceeded)):
            return {"kind": "succeeded"}
        return value

    return NativeAction(
        Tool(name=name, description=name.replace(".", " "), inputSchema=model.model_json_schema()), invoke
    )


def _credential(credentials: JsonObject, name: str) -> str:
    value = credentials.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError("native_credentials_unavailable")
    return value


def native_actions(
    provider: str,
    context: JsonObject,
    policy: JsonObject,
    configuration: JsonObject,
    credentials: JsonObject,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> dict[str, NativeAction]:
    if provider == "slack":
        binding = SlackActionBinding.model_validate(
            {
                "channel_id": context.get("channel_id"),
                "root_thread_ts": context.get("root_thread_ts"),
                "conversation_kind": context.get("conversation_kind"),
                "reply_mode": policy.get("reply_mode", "thread"),
            }
        )
        client = SlackNativeClient(http)
        token = _credential(credentials, "bot_token")
        reply = partial(client.reply, binding, bot_token=token, request_id=new_object_id("reply"))
        actions = (
            action("slack.reply", SlackAutoReplyArguments, reply)
            if binding.reply_mode == "auto"
            else action("slack.reply", SlackForcedReplyArguments, reply),
            action(
                "slack.list_members", SlackListMembersArguments, partial(client.list_members, binding, bot_token=token)
            ),
            action(
                "slack.read_messages",
                SlackReadMessagesArguments,
                partial(client.read_messages, binding, bot_token=token),
            ),
        )
    elif provider == "lark":
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
            app_secret=_credential(credentials, "app_secret"),
        )
        client = LarkNativeClient(http, endpoints, tokens, open_api_origin=config.open_api_origin)
        reply = partial(client.reply, binding, request_id=new_object_id("reply"), effect_id=new_object_id("effect"))
        actions = (
            action("lark.reply", LarkAutoReplyArguments, reply)
            if binding.reply_mode == "auto"
            else action("lark.reply", LarkForcedReplyArguments, reply),
            action("lark.list_members", LarkListMembersArguments, partial(client.list_members, binding)),
            action("lark.read_messages", LarkReadMessagesArguments, partial(client.read_messages, binding)),
        )
    elif provider == "github":
        config = GitHubAccountConfig.model_validate(configuration)
        binding = GitHubActionBinding.model_validate(
            {
                "repository_id": context.get("repository_id"),
                "owner": context.get("repository_owner"),
                "repository": context.get("repository_name"),
                "number": context.get("number"),
                "target_kind": context.get("target_kind"),
            }
        )
        tokens = GitHubInstallationTokenProvider(
            http,
            endpoints,
            api_origin=config.api_origin,
            app_id=config.app_id,
            installation_id=config.installation_id,
            private_key_pem=_credential(credentials, "app_private_key_pem"),
            permissions={"issues": "write", "pull_requests": "write"},
        )
        client = GitHubNativeClient(http, endpoints, tokens, api_origin=config.api_origin, web_origin=config.web_origin)
        actions = (
            action(
                "github.add_comment",
                GitHubAddCommentArguments,
                partial(client.add_comment, binding, request_id=new_object_id("comment")),
            ),
            action("github.read_comments", GitHubReadCommentsArguments, partial(client.read_comments, binding)),
            action("github.read_issue_or_pr", GitHubReadTargetArguments, partial(client.read_issue_or_pr, binding)),
        )
        if binding.target_kind == "pull_request":
            actions += (
                action("github.list_pr_files", GitHubListPrFilesArguments, partial(client.list_pr_files, binding)),
            )
    else:
        raise ValueError("native_provider_unavailable")
    return {item.definition.name: item for item in actions}
