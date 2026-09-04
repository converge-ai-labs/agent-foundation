"""Provider-specific authorization scopes for proactive account tools."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from a13n_service.connectivity.domain import JsonObject

ProviderId = Annotated[str, StringConstraints(min_length=1, max_length=256)]
RepositoryName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SlackScope(StrictModel):
    channel_ids: tuple[ProviderId, ...] = Field(max_length=128)


class LarkScope(StrictModel):
    chat_ids: tuple[ProviderId, ...] = Field(max_length=128)


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


SCOPES = {"slack": SlackScope, "lark": LarkScope, "github": GitHubScope}
TOOLS = {
    "slack": frozenset({"slack.send_message"}),
    "lark": frozenset({"lark.send_message"}),
    "github": frozenset(
        {"github.add_comment", "github.read_comments", "github.read_issue_or_pr", "github.list_pr_files"}
    ),
}


def validate_scope(provider: str, scope: JsonObject, tools: tuple[str, ...]) -> None:
    schema = SCOPES.get(provider)
    if schema is None or not set(tools) <= TOOLS[provider]:
        raise ValueError("unsupported_account_tools")
    schema.model_validate(scope)
