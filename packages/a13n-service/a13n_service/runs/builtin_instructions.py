"""Current deployment guidance bound once when the Host prepares an attempt."""

from dataclasses import dataclass

from a13n_harness import AgentContext
from pydantic_ai.capabilities import AbstractCapability


@dataclass
class BuiltinInstructionsCapability(AbstractCapability[AgentContext]):
    instructions: str

    @classmethod
    def get_serialization_name(cls) -> None:
        return None

    def get_instructions(self) -> str:
        return self.instructions
