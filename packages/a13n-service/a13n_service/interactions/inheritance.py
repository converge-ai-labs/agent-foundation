"""Exact execution selections retained by Feedback, waiting Continue, and Retry."""

from typing import TypedDict

from a13n_service.iam import PrincipalRef
from a13n_service.models.domain import ModelExecutionObservation

from .domain import JsonObject, Run


class InheritedRunFields(TypedDict):
    authority_principal: PrincipalRef
    agent_id: str
    agent_revision_id: str
    effective_agent_config_digest: str
    model_execution_observation: ModelExecutionObservation
    connection_selections: tuple[JsonObject, ...]
    native_tool_contexts: tuple[JsonObject, ...]


def inherited_run_fields(source: Run) -> InheritedRunFields:
    """Construct and validate inherited authority from the same explicit fields."""
    return InheritedRunFields(
        authority_principal=source.authority_principal,
        agent_id=source.agent_id,
        agent_revision_id=source.agent_revision_id,
        effective_agent_config_digest=source.effective_agent_config_digest,
        model_execution_observation=source.model_execution_observation,
        connection_selections=source.connection_selections,
        native_tool_contexts=source.native_tool_contexts,
    )
