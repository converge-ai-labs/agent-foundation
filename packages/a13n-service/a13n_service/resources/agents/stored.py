"""Read frozen Agent configuration without reauthoring it; reject unavailable execution contracts.

Run options and Schedule snapshots embed the same configuration types, so both boundaries
apply recursively to their owning models. Reads never translate obsolete selections.
"""

from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.reads import UnavailableConfiguration
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride, apply_override

_READ_CONTEXT: dict[str, object] = {"preserve_toolsets": True}


def read[T: BaseModel](model: type[T], value: object, *, kind: str, id: str) -> T:
    """Deserialize a stored configuration or containing view for inspection only."""
    try:
        return model.model_validate(value, context=_READ_CONTEXT)
    except ValidationError as error:
        raise conflict(kind, id, "configuration_unavailable") from error


def require[T: BaseModel](model: type[T], value: object, *, kind: str, id: str) -> T:
    """Require current contracts before executing or republishing stored configuration."""
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise conflict(kind, id, "configuration_unavailable") from error


def read_effective(config: AgentConfig, override: AgentOverride | None, *, run_id: str) -> AgentConfig:
    """Inspect the frozen effective configuration without validating today's catalog."""
    try:
        return config if override is None else apply_override(config, override, validation_context=_READ_CONTEXT)
    except ValidationError as error:
        raise conflict("run", run_id, "configuration_unavailable") from error


def require_effective(config: AgentConfig, override: AgentOverride | None, *, revision_id: str) -> AgentConfig:
    """Validate a stored effective execution configuration, including override reviewer rules."""
    try:
        return config if override is None else apply_override(config, override)
    except ValidationError as error:
        raise conflict("agent_revision", revision_id, "configuration_unavailable") from error


def batch_item[T](read_item: Callable[[], T]) -> T | UnavailableConfiguration:
    """Isolate only obsolete configuration; authorization and infrastructure failures still propagate."""
    try:
        return read_item()
    except ServiceError as error:
        if error.code != "conflict" or error.details.get("reason") != "configuration_unavailable":
            raise
        return UnavailableConfiguration.model_validate(error.details)
