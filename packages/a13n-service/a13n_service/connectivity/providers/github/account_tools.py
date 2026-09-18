"""GitHub Account actions restricted to exact installation repositories."""

from functools import partial
from typing import Annotated, Literal

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from mcp.types import Tool
from pydantic import Field, StringConstraints, model_validator

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.native_actions import NativeAction
from a13n_service.connectivity.providers.github.actions import (
    GitHubAddCommentArguments,
    GitHubListPrFilesArguments,
    GitHubReadCommentsArguments,
    GitHubReadTargetArguments,
)

from ...accounts.domain import StrictModel
from ..tool_contracts import AccountTools
from .inbound_tools import inbound_actions

RepositoryName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]


class GitHubRepository(StrictModel):
    repository_id: int = Field(gt=0)
    owner: RepositoryName
    repository: RepositoryName


class GitHubScope(StrictModel):
    repositories: tuple[GitHubRepository, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def unique_repositories(self) -> "GitHubScope":
        if len({item.repository_id for item in self.repositories}) != len(self.repositories):
            raise ValueError("Repository IDs must be unique")
        return self


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


_ARGUMENTS: dict[str, type[GitHubTarget]] = {
    "github.add_comment": GitHubCommentArguments,
    "github.read_comments": GitHubCommentsArguments,
    "github.read_issue_or_pr": GitHubReadArguments,
    "github.list_pr_files": GitHubFilesArguments,
}


def actions(
    configuration: JsonObject,
    credentials: JsonObject,
    target_scope: JsonObject,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> dict[str, NativeAction]:
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
        actions = inbound_actions(context, {}, configuration, credentials, http, endpoints)
        selected = actions.get(name)
        if selected is None:
            raise ValueError("account_action_unavailable")
        return await selected.call(
            {key: value for key, value in arguments.items() if key not in GitHubTarget.model_fields}
        )

    return {
        name: NativeAction(
            Tool(name=name, description=name.replace(".", " "), input_schema=model.model_json_schema()),
            partial(invoke, name, model),
        )
        for name, model in _ARGUMENTS.items()
    }


ACCOUNT_TOOLS = AccountTools(GitHubScope, frozenset(_ARGUMENTS), actions)
