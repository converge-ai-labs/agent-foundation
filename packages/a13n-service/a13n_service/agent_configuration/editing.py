"""Bounded, all-or-nothing edits to complete Agent configurations.

This module validates structure only. The application boundary must resolve and
authorize every dependency before publishing the returned candidate to a draft.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, ValidationError

from a13n_service.agents.domain import AgentConfig, StrictModel
from a13n_service.application_errors import ApplicationError, ErrorCategory

PathPart = Annotated[str, StringConstraints(min_length=1, max_length=128)]
ConfigPath = Annotated[tuple[PathPart, ...], Field(max_length=16)]


class SetOperation(StrictModel):
    op: Literal["set"]
    path: ConfigPath
    value: JsonValue


class RemoveOperation(StrictModel):
    op: Literal["remove"]
    path: ConfigPath


class ReplaceTextOperation(StrictModel):
    op: Literal["replace_text"]
    path: ConfigPath
    old_text: Annotated[str, StringConstraints(min_length=1, max_length=256 * 1024)]
    new_text: Annotated[str, StringConstraints(max_length=256 * 1024)]


Operation = Annotated[SetOperation | RemoveOperation | ReplaceTextOperation, Field(discriminator="op")]


class UpdateDraftRequest(StrictModel):
    expected_version: int = Field(ge=1)
    operations: tuple[Operation, ...] = Field(min_length=1, max_length=32)


def invalid_edit(message: str, *, index: int | None = None) -> ApplicationError:
    return ApplicationError(
        "configuration_edit_invalid",
        message,
        category=ErrorCategory.invalid_input,
        details={} if index is None else {"operation_index": index},
    )


def edit_config(config: AgentConfig | None, operations: tuple[Operation, ...]) -> AgentConfig:
    """Apply to detached data; never mutate the caller's accepted configuration."""
    if not 1 <= len(operations) <= 32:
        raise invalid_edit("A command requires between 1 and 32 operations.")
    candidate = None if config is None else config.model_dump(mode="json", by_alias=True)
    for index, operation in enumerate(operations):
        if not operation.path:
            if not isinstance(operation, SetOperation) or not isinstance(operation.value, dict):
                raise invalid_edit("Only set with a complete configuration can replace the root.", index=index)
            replacement = deepcopy(operation.value)
            if candidate is not None and candidate.keys() - replacement.keys():
                raise invalid_edit("Complete replacement must explicitly retain or clear existing fields.", index=index)
            candidate = replacement
            continue
        if candidate is None:
            raise invalid_edit("Initialize the complete configuration before editing fields.", index=index)
        parent: JsonValue = candidate
        for part in operation.path[:-1]:
            if not isinstance(parent, dict) or part not in parent:
                raise invalid_edit("The path must traverse existing configuration objects.", index=index)
            parent = parent[part]
        if not isinstance(parent, dict):
            raise invalid_edit("Arrays must be replaced as complete values.", index=index)
        key = operation.path[-1]
        if isinstance(operation, SetOperation):
            parent[key] = deepcopy(operation.value)
        elif isinstance(operation, RemoveOperation):
            if key not in parent:
                raise invalid_edit("The field to remove does not exist.", index=index)
            del parent[key]
        else:
            value = parent.get(key)
            if not isinstance(value, str) or value.count(operation.old_text) != 1:
                raise invalid_edit("Text replacement requires exactly one literal match in a string.", index=index)
            parent[key] = value.replace(operation.old_text, operation.new_text, 1)
    try:
        return AgentConfig.model_validate(candidate)
    except ValidationError as error:
        # Validation inputs and exception text may contain credentials or prompts.
        raise invalid_edit("The complete candidate does not satisfy the Agent configuration contract.") from error
