"""Github inbound tools bound to the admitted conversation."""

from functools import partial

import httpx2

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.ids import new_object_id

from ...domain import JsonObject
from ...native_actions import NativeAction, action, credential
from ..github.actions import (
    GitHubActionBinding,
    GitHubAddCommentArguments,
    GitHubListPrFilesArguments,
    GitHubReadCommentsArguments,
    GitHubReadTargetArguments,
)
from ..github.adapter import GitHubAccountConfig
from ..github.client import GitHubNativeClient
from ..github.token import GitHubInstallationTokenProvider


def inbound_actions(
    context: JsonObject,
    policy: JsonObject,
    configuration: JsonObject,
    credentials: JsonObject,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> dict[str, NativeAction]:
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
        private_key_pem=credential(credentials, "app_private_key_pem"),
        permissions={"issues": "write", "pull_requests": "write"},
    )
    client = GitHubNativeClient(http, endpoints, tokens, api_origin=config.api_origin, web_origin=config.web_origin)

    async def add_comment(arguments):
        return await client.add_comment(binding, arguments, request_id=new_object_id("comment"))

    actions = (
        action("github.add_comment", GitHubAddCommentArguments, add_comment, hide_receipt=True),
        action("github.read_comments", GitHubReadCommentsArguments, partial(client.read_comments, binding)),
        action("github.read_issue_or_pr", GitHubReadTargetArguments, partial(client.read_issue_or_pr, binding)),
    )
    if binding.target_kind == "pull_request":
        actions += (action("github.list_pr_files", GitHubListPrFilesArguments, partial(client.list_pr_files, binding)),)
    return {item.definition.name: item for item in actions}
