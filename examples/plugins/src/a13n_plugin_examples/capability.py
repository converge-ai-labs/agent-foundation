"""A declarative custom Capability suitable for trusted Host authorization."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_harness import AgentContext
from pydantic_ai.capabilities import AbstractCapability

CAPABILITY_SERIALIZATION_NAME = "example_instructions"


@dataclass
class ExampleInstructionsCapability(AbstractCapability[AgentContext]):
    """Contribute one package-owned instruction block to the Agent loop."""

    instructions: str = "Explain material assumptions before the answer."
    id: str | None = "example-instructions"

    @classmethod
    def get_serialization_name(cls) -> str:
        return CAPABILITY_SERIALIZATION_NAME

    def get_instructions(self) -> str:
        return self.instructions
