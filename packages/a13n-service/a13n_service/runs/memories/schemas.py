"""Thread memory mounts as the API returns them; a mount itself is `MemoryMount`."""

from pydantic import BaseModel

from a13n_service.resources.memories.schemas import MemoryMount


class MemoryMountPage(BaseModel):
    items: list[MemoryMount]
    next_cursor: str | None = None
