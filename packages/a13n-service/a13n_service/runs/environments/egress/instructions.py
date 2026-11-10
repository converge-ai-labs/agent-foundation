"""Non-secret usage guidance from the Run's frozen Session selection."""

from collections.abc import Sequence
from dataclasses import dataclass

from a13n_harness import AgentContext
from a13n_harness.environment.models import EnvironmentAction
from pydantic_ai.capabilities import AbstractCapability

from a13n_service.runs.schemas import EnvironmentMount


def render(mounts: Sequence[EnvironmentMount]) -> str | None:
    bindings: list[str] = []
    for mount in mounts:
        if EnvironmentAction.SHELL_EXEC not in mount.permission_ceiling.operations:
            continue
        for reference in mount.session_configuration.credential_references:
            bindings.append(
                f"- Environment mount `{mount.name}`: `${reference.env}` may be used in HTTPS request headers "
                f"to {', '.join(reference.inject_hosts)}. For a service accepting Bearer authentication, use "
                f'`curl -H "Authorization: Bearer ${{{reference.env}}}" https://{reference.inject_hosts[0]}/`. '
                "Choose the endpoint and authentication scheme required by that service."
            )
    if not bindings:
        return None
    return "\n".join(
        (
            "Configured HTTPS credentials for this Run:",
            *bindings,
            "Availability is checked when the environment is prepared. Commands receive placeholders; "
            "envd replaces them with credentials only in HTTPS headers to the configured hosts. "
            "Use the environment variable directly without printing it, redefining it, or copying it into files. "
            "These bindings apply to commands in the named environment, not Host web or browser tools. "
            "Do not disable TLS verification; use the environment's configured certificate trust. "
            "Credential rotation does not require changing commands or retrieving a new value.",
        )
    )


@dataclass
class CredentialInstructionsCapability(AbstractCapability[AgentContext]):
    """Native replaceable instructions, separate from compressible conversation content."""

    id = "a13n.service.egress-credentials"
    instructions: str

    @classmethod
    def get_serialization_name(cls) -> None:
        return None

    def get_instructions(self) -> str:
        return self.instructions
