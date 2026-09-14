"""Metadata-only Thread creation contract."""

from pydantic import Field

from a13n_service.environments.domain import EnvironmentSelection
from a13n_service.labels import Labels

from .domain import ObjectId, StrictModel


class CreateThreadRequest(StrictModel):
    agent_id: ObjectId | None = None
    session_id: ObjectId | None = None
    environment: EnvironmentSelection | None = None
    session_labels: Labels = Field(default_factory=dict)
    labels: Labels = Field(default_factory=dict)
