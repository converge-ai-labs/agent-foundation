"""Shared projections of canonical Run state, independent of chat platforms."""

from dataclasses import dataclass

from a13n_service.interactions.domain import RunStatus

SEALED = frozenset({RunStatus.waiting, RunStatus.completed, RunStatus.failed, RunStatus.cancelled})


@dataclass(frozen=True)
class TaskProgress:
    run_id: str
    status: RunStatus
    stopping: bool = False
    details_url: str | None = None

    @property
    def sealed(self) -> bool:
        return self.status in SEALED

    @property
    def display_status(self) -> str:
        return "stopping" if self.stopping and not self.sealed else self.status.value
