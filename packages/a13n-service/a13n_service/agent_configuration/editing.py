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

from .errors import EDIT_REASONS, validation_issues

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


def invalid_edit(reason: str, *, index: int | None = None) -> ApplicationError:
    return ApplicationError(
        "configuration_edit_invalid",
        EDIT_REASONS[reason],
        category=ErrorCategory.invalid_input,
        details={"reason": reason, **({} if index is None else {"operation_index": index})},
    )


def edit_config(config: AgentConfig | None, operations: tuple[Operation, ...]) -> AgentConfig:
    """Apply to detached data; never mutate the caller's accepted configuration."""
    if not 1 <= len(operations) <= 32:
        raise invalid_edit("operation_count")
    candidate = None if config is None else config.model_dump(mode="json", by_alias=True)
    for index, operation in enumerate(operations):
        if not operation.path:
            if not isinstance(operation, SetOperation) or not isinstance(operation.value, dict):
                raise invalid_edit("root_set_required", index=index)
            replacement = deepcopy(operation.value)
            if candidate is not None and candidate.keys() - replacement.keys():
                raise invalid_edit("replacement_incomplete", index=index)
            candidate = replacement
            continue
        if candidate is None:
            raise invalid_edit("initialization_required", index=index)
        parent: JsonValue = candidate
        for part in operation.path[:-1]:
            if not isinstance(parent, dict) or part not in parent:
                raise invalid_edit("parent_missing", index=index)
            parent = parent[part]
        if not isinstance(parent, dict):
            raise invalid_edit("object_required", index=index)
        key = operation.path[-1]
        if isinstance(operation, SetOperation):
            parent[key] = deepcopy(operation.value)
        elif isinstance(operation, RemoveOperation):
            if key not in parent:
                raise invalid_edit("field_missing", index=index)
            del parent[key]
        else:
            value = parent.get(key)
            if not isinstance(value, str) or value.count(operation.old_text) != 1:
                raise invalid_edit("text_match_required", index=index)
            parent[key] = value.replace(operation.old_text, operation.new_text, 1)
    try:
        return AgentConfig.model_validate(candidate)
    except ValidationError as error:
        # Validation inputs and exception text may contain credentials or prompts.
        raise ApplicationError(
            "configuration_edit_invalid",
            "The complete candidate does not satisfy the Agent configuration contract.",
            category=ErrorCategory.invalid_input,
            details={"issues": validation_issues(error)},
        ) from error
