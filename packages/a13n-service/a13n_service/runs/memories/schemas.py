"""Thread memory mounts as the API accepts and returns them; a mount itself is `MemoryMount`."""

from a13n_harness.providers.memory import MemoryAccess
from pydantic import BaseModel, ConfigDict

from a13n_service.resources.memories.schemas import MemoryMount


class MemoryMountUpdate(BaseModel):
    """Fields left out stay unchanged."""

    model_config = ConfigDict(extra="forbid")
    access: MemoryAccess | None = None
    recall: bool | None = None


class MemoryMountPage(BaseModel):
    items: list[MemoryMount]
    next_cursor: str | None = None
