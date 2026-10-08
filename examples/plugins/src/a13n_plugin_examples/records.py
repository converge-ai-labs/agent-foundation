"""Neutral result records shared by plugin demos without importing plugin targets."""

from dataclasses import dataclass
from typing import Literal

type ObservedRunStatus = Literal["completed", "suspended", "failed", "cancelled", "interrupted"]


@dataclass(frozen=True, slots=True)
class RunObservation:
    """A deliberately small record that does not retain prompts or model output."""

    run_id: str
    status: ObservedRunStatus
    model_requests: int
