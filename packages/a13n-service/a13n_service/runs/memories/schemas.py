"""Thread memory mounts as the API accepts and returns them; a mount itself is `MemoryMount`."""

from a13n_harness.providers.memory import MemoryAccess
from pydantic import BaseModel, ConfigDict

from a13n_service.resources.memories.schemas import MemoryMount


class RunMemoryMount(MemoryMount):
    """Host-resolved Space identity and roots, never accepted from a mount request."""

    model_config = ConfigDict(extra="ignore")

    space_id: str | None = None
    roots: tuple[str, ...] = ()

    @property
    def cursor_key(self) -> str:
        return self.memory_id if self.space_id is None else f"{self.space_id}:{','.join(self.roots)}"


class FrozenMemoryMount(RunMemoryMount):
    """Internal acceptance snapshot; guidance is never part of the public Run mount."""

    model_config = ConfigDict(extra="forbid")
    file_guide: str | None = None
    timezone: str = "UTC"


class MemoryMountUpdate(BaseModel):
    """Fields left out stay unchanged."""

    model_config = ConfigDict(extra="forbid")
    access: MemoryAccess | None = None
    recall: bool | None = None


class MemoryMountPage(BaseModel):
    items: list[MemoryMount]
    next_cursor: str | None = None
