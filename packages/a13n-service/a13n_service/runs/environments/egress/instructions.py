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
                f"- `{mount.name}`: `${{{reference.env}}}` for {', '.join(reference.inject_hosts)}. "
                f'Bearer example: `curl -H "Authorization: Bearer ${{{reference.env}}}" '
                f"https://{reference.inject_hosts[0]}/`."
            )
    if not bindings:
        return None
    return "\n".join(
        (
            "Configured HTTPS credentials for this Run:",
            *bindings,
            "Use the service's authentication scheme. Values are placeholders; envd substitutes credentials only "
            "in HTTPS headers to these hosts. Shell commands only, not Host web/browser tools. "
            "Use variables directly without printing, overwriting, or saving them. Keep TLS verification enabled. "
            "Availability is checked when the environment is prepared; rotation is automatic.",
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
