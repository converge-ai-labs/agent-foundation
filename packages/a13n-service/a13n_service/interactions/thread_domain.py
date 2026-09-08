"""Metadata-only Thread creation contract."""

from a13n_service.environments.domain import EnvironmentSelection

from .domain import ObjectId, StrictModel


class CreateThreadRequest(StrictModel):
    agent_id: ObjectId | None = None
    session_id: ObjectId | None = None
    environment: EnvironmentSelection | None = None
