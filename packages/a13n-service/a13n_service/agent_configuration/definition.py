"""Bounded deployment definitions; editable Agent data cannot select host authority."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, JsonValue, ValidationError

from .context import StrictModel
from .persistence import failure

ASSETS = Path(__file__).parent / "assets"
READ_TOOLS = frozenset({"view", "ls", "glob", "grep"})
CONFIGURATION_TOOLS = frozenset(
    {
        "get_configuration_draft",
        "update_configuration_draft",
        "search_configuration_resources",
        "get_configuration_resource",
        "read_interaction_run",
        "start_run",
        "ask_user_question",
    }
)


class ModelPreference(StrictModel):
    family: str = Field(min_length=1, max_length=128)
    upstream_models: tuple[str, ...] = Field(min_length=1, max_length=16)
    model_apis: tuple[str, ...] = Field(min_length=1, max_length=8)
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    required_thinking_effort: Literal["high"] | None = None


class AssistantDefinition(StrictModel):
    schema_version: Literal["1"]
    name: Literal["Agent Configuration Assistant"]
    instructions: str = Field(min_length=1, max_length=32 * 1024)
    tools: tuple[str, ...] = Field(min_length=1, max_length=32)
    preferences: tuple[ModelPreference, ...] = Field(min_length=1, max_length=16)
    request_limit: int = Field(ge=1, le=100)
    total_tokens_limit: int = Field(ge=1)


class _DefinitionLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("Expected a mapping.")
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)):
            raise ValueError("Definition keys must be unique strings.")
        return super().construct_mapping(node, deep=deep)

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("Definition aliases are not supported.")
        return super().compose_node(parent, index)


def parse_definition(content: bytes) -> AssistantDefinition:
    if len(content) > 64 * 1024:
        raise failure("configuration_definition_invalid", "The deployed assistant definition is invalid.")
    try:
        value = AssistantDefinition.model_validate(yaml.load(content, Loader=_DefinitionLoader))
        if len(value.tools) != len(set(value.tools)) or set(value.tools) != READ_TOOLS | CONFIGURATION_TOOLS:
            raise ValueError("The assistant tool composition is not supported.")
        return value
    except (yaml.YAMLError, ValueError, TypeError, RecursionError, ValidationError) as error:
        raise failure("configuration_definition_invalid", "The deployed assistant definition is invalid.") from error


def load_definition(*, total_tokens_limit: int | None = None) -> AssistantDefinition:
    definition = parse_definition((ASSETS / "assistant.yaml").read_bytes())
    if total_tokens_limit is None:
        return definition
    return AssistantDefinition.model_validate({**definition.model_dump(), "total_tokens_limit": total_tokens_limit})
