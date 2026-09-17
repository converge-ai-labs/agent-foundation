"""Authorized client tickets and safe live connection observations."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import Field

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor
from a13n_service.ids import ObjectId

from ..domain import DomainModel
from ..errors import EnvironmentManagementError
from .coordination import ConfirmedObservation, ConnectionCoordination, CoordinationError
from .resources import ConnectionResources


class ClientConnectionTicket(DomainModel):
    ticket: str = Field(repr=False)
    expires_at: datetime
    websocket_url: str
    connection_id: ObjectId


class ClientConnectionStatus(DomainModel):
    status: Literal["online", "connecting", "offline"]
    connection_id: ObjectId | None
    observed_at: datetime
    error: Literal["environment_unavailable", "environment_initialization_failed", "control_draining"] | None


def connection_dependency_unavailable() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_coordination_unavailable",
        "Client Environment connection coordination is unavailable.",
        category=ErrorCategory.unavailable,
    )


def websocket_origin(value: str) -> str:
    """Accept only an operator-configured credential-free WSS origin."""
    parsed = urlsplit(value)
    if (
        value != value.strip()
        or len(value) > 2048
        or any(character.isspace() or ord(character) < 32 for character in value)
        or parsed.scheme != "wss"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or "\\" in value
        or "%" in parsed.netloc
    ):
        raise ValueError("Client Environment public origin must be a credential-free WSS origin")
    # Accessing port validates its syntax and range, including bracketed IPv6.
    if parsed.port == 0:
        raise ValueError("Client Environment public origin port must be positive")
    return f"wss://{parsed.netloc}"


class ClientConnectionService:
    def __init__(
        self, resources: ConnectionResources, coordination: ConnectionCoordination, *, public_origin: str
    ) -> None:
        self.resources = resources
        self.coordination = coordination
        self.public_origin = websocket_origin(public_origin)

    async def issue_ticket(self, actor: AuthenticatedActor, environment_id: str) -> ClientConnectionTicket:
        target = await self.resources.authorized(actor, environment_id, manage=True)
        try:
            ticket = await self.coordination.issue(target.organization_id, target.environment_id)
        except CoordinationError as error:
            raise connection_dependency_unavailable() from error
        return ClientConnectionTicket(
            ticket=ticket.secret,
            expires_at=datetime.fromtimestamp(ticket.expires_at_ms / 1000, UTC),
            websocket_url=f"{self.public_origin}/api/v1/environments/{quote(target.environment_id, safe='')}/connect",
            connection_id=ticket.connection_id,
        )

    async def status(self, actor: AuthenticatedActor, environment_id: str) -> ClientConnectionStatus:
        target = await self.resources.authorized(actor, environment_id)
        observation = (await self.observe(target.organization_id, target.environment_id)).value
        safe_error = observation.error
        if safe_error not in (None, "environment_unavailable", "environment_initialization_failed", "control_draining"):
            safe_error = "environment_unavailable"
        # Disabled Providers remain readable, but cannot promise eligibility.
        disabled = not target.provider_enabled
        return ClientConnectionStatus(
            status="offline" if disabled else observation.status,
            connection_id=(
                observation.connection.connection_id if observation.connection is not None and not disabled else None
            ),
            observed_at=datetime.fromtimestamp(observation.now_ms / 1000, UTC),
            error="environment_unavailable" if disabled else safe_error,
        )

    async def observe(self, organization_id: str, environment_id: str) -> ConfirmedObservation:
        """Never advertise an online grant already expired in transit."""
        try:
            for _ in range(2):
                observation = await self.coordination.observe(organization_id, environment_id)
                if observation.value.status == "offline" or observation.deadline().monotonic_at > monotonic():
                    return observation
        except CoordinationError as error:
            raise connection_dependency_unavailable() from error
        raise connection_dependency_unavailable()
