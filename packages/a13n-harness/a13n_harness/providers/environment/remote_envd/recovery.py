"""Host-private, best-effort HTTP Session attachment; never operation replay."""

from dataclasses import dataclass, field

from a13n_envd_client import EIPDeviceConnection, EIPSession, EIPSessionStateError
from a13n_envd_client.eip.v1 import ErrorType, SessionDescriptor
from a13n_envd_client.errors import EIPMethodError
from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.environment.envd_policy import EnvdBoundaryRequirement


class HttpEnvdSessionState(BaseModel):
    """An internal Host checkpoint hint, not portable EnvironmentState or a credential."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str
    descriptor: SessionDescriptor = Field(repr=False)
    working_directory: str | None = None


@dataclass(slots=True)
class HttpEnvdSessionRecovery:
    """One Host execution scope's saved hint and current checkpoint snapshot.

    The Host restores this only within the original logical execution scope.
    Attachment does not fence another authenticated client or extend Session expiry.
    Normal execution close still closes the Session, including after attachment.
    """

    state: HttpEnvdSessionState | None = field(default=None, repr=False)

    async def attach(
        self,
        device: EIPDeviceConnection,
        *,
        endpoint: str,
        expected_device_id: str,
        working_directory: str | None,
        required_methods: frozenset[str],
        expected_boundary: EnvdBoundaryRequirement | None,
    ) -> EIPSession | None:
        previous, self.state = self.state, None
        if device.descriptor.device_id != expected_device_id:
            raise EIPSessionStateError("Session recovery belongs to another Device")
        if expected_boundary is not None:
            expected_boundary.check(device.descriptor.boundary)
        if previous is None:
            return None
        descriptor = previous.descriptor
        if (
            previous.endpoint != endpoint
            or descriptor.device_id != expected_device_id
            or descriptor.generation != device.descriptor.generation
            or previous.working_directory != working_directory
        ):
            return None
        if descriptor.boundary != device.descriptor.boundary:
            raise EIPSessionStateError("Recovered Session execution boundary changed")
        if descriptor.egress is not None:
            # Controlled policies require current secret resolution, not a saved descriptor.
            return None
        if required_methods - set(descriptor.available_methods):
            return None
        try:
            return await device.attach_session(descriptor)
        except EIPMethodError as error:
            # For HTTP, attachment has no per-connection owner: this means the
            # Session is closed/expired. Transport and compatibility failures
            # remain failures, not evidence that allocating another is safe.
            if error.error.data.error_type != ErrorType.NOT_INITIALIZED:
                raise
            return None

    def observe(self, endpoint: str, working_directory: str | None, session: EIPSession) -> None:
        self.state = HttpEnvdSessionState(
            endpoint=endpoint, descriptor=session.descriptor, working_directory=working_directory
        )

    def closed(self, session: EIPSession) -> None:
        if self.state is not None and self.state.descriptor.session_id == session.session_id:
            self.state = None
