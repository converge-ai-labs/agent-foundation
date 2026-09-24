"""Memories, their files, revisions and records as the API accepts and returns them, and the mount shape threads,
agent defaults and runs share."""

from datetime import datetime
from typing import Annotated, Literal

from a13n_harness.providers.memory import MemoryAccess
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels
from a13n_service.resources.memories.tables import MemoryKind

MemoryKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")]
MemoryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Description = Annotated[str, StringConstraints(max_length=2048)]
# The byte bound is `memory.guide_bytes`.
Guide = Annotated[str, StringConstraints(max_length=65536)]
# The canonical form and byte bound follow the file format of `memory.*` settings.
FilePath = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
# Checked as file paths when a memory is created or changed; a path need not exist.
AlwaysLoad = Annotated[tuple[FilePath, ...], Field(max_length=64)]


def _printable(value: str) -> str:
    if not value.isprintable():
        raise ValueError("must be printable")
    return value


# A record memory's namespace in its provider's backend, such as a mem0 `user_id`, which mem0 filters would widen
# with `*`.
Namespace = Annotated[
    str, StringConstraints(min_length=1, max_length=256, pattern=r"^[^\s*]+$"), AfterValidator(_printable)
]
# The byte bound is `memory.record_chars`; mem0 accepts at most 8000 characters.
RecordText = Annotated[str, StringConstraints(min_length=1, max_length=8000)]
# The Harness mount-name rule: the model addresses a memory by it.
MountName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]
# Memories one mount list holds at most; `memory.mounts_per_thread` bounds a thread's.
MAX_MOUNTS = 32


class MemoryCreate(BaseModel):
    """`postgres` makes a file memory the Service stores; a Memory Provider's type makes a record memory in that
    provider's backend, under a new namespace or the existing one `namespace` adopts."""

    model_config = ConfigDict(extra="forbid")
    key: MemoryKey
    name: MemoryName
    description: Description | None = None
    labels: Labels = Field(default_factory=dict)
    type: str = Field(default="postgres", min_length=1, max_length=64)
    provider_id: ObjectId | None = None
    namespace: Namespace | None = None
    # Null inherits the deployment's default guide; "" gives the memory none.
    guide: Guide | None = None
    # File memories only.
    always_load: AlwaysLoad = ()


class MemoryUpdate(BaseModel):
    """Fields left out stay unchanged; `description: null` clears it and `guide: null` inherits the default."""

    model_config = ConfigDict(extra="forbid")
    name: MemoryName | None = None
    description: Description | None = None
    labels: Labels | None = None
    guide: Guide | None = None
    always_load: AlwaysLoad | None = None


class Memory(BaseModel):
    id: str
    organization_id: str
    workspace_id: str
    key: str
    name: str
    description: str | None
    kind: MemoryKind
    # `postgres`, or the Memory Provider type of a record memory.
    type: str
    provider_id: str | None
    namespace: str | None
    guide: str | None
    # What a null `guide` resolves to for the memory's kind, shown even while the memory has its own.
    inherited_guide: str
    always_load: list[str]
    labels: dict[str, str]
    # A file memory's files, current content and retained history, which together stay within
    # `memory.max_total_bytes`; null for a record memory.
    file_count: int | None
    content_bytes: int | None
    history_bytes: int | None
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class MemoryPage(BaseModel):
    items: list[Memory]
    next_cursor: str | None


class MemoryFileEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    path: str
    # The number of the file's last change; the file's ETag is `"{id}:{version}"`.
    version: int
    size: int
    # The frontmatter `description`, else the first non-empty line.
    description: str | None
    updated_by_run_id: str | None
    updated_by_principal_id: str | None
    created_at: datetime
    updated_at: datetime


class MemoryFile(MemoryFileEntry):
    content: str


class MemoryFilePage(BaseModel):
    items: list[MemoryFileEntry]
    next_cursor: str | None


class MemoryFileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: FilePath
    content: str


class MemoryFileReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str


class MemoryFileMove(BaseModel):
    """Moves the file `If-Match` names to a free path; it keeps its ID."""

    model_config = ConfigDict(extra="forbid")
    source: FilePath
    destination: FilePath


class MemoryRevision(BaseModel):
    """One change to one path. A move is two: `move_out` at the source and `move_in` at the destination."""

    model_config = ConfigDict(from_attributes=True)
    seq: int
    path: str
    op: Literal["create", "update", "delete", "move_out", "move_in"]
    # The other path of a move.
    moved_path: str | None
    run_id: str | None
    tool_call_id: str | None
    principal_id: str | None
    created_at: datetime


class MemoryRevisionDetail(MemoryRevision):
    # The path's content before and after the change; null where no file was.
    previous_content: str | None
    content: str | None
    # Unified diff hunks, each a `@@ -a,b +c,d @@` header followed by its lines.
    hunks: list[str]


class MemoryRevisionPage(BaseModel):
    items: list[MemoryRevision]
    next_cursor: str | None


class MemoryFileState(BaseModel):
    """A path after a restore: its file, or null when the restored state is no file."""

    path: str
    file: MemoryFile | None


class HistoryPurge(BaseModel):
    purged: int


class MemoryRecordView(BaseModel):
    """A record as the memory's provider returns it; `score` is its similarity to a search query."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    text: str
    score: float | None = None
    updated_at: datetime | None = None


class MemoryRecordPage(BaseModel):
    items: list[MemoryRecordView]
    # The provider's own cursor, passed back unchanged; a search answers without one.
    next_cursor: str | None


class MemoryRecordText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: RecordText


class MemoryRecordSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: RecordText
    limit: int = Field(default=10, ge=1, le=100)


class MemoryMount(BaseModel):
    """A memory under the name the model addresses it by, exposing the tools its access allows. `recall` lets a
    record memory recall records into each run's first input; file memories ignore it."""

    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)
    name: MountName
    memory_id: ObjectId
    access: MemoryAccess
    recall: bool = True


def _unique(mounts: tuple[MemoryMount, ...]) -> tuple[MemoryMount, ...]:
    names, memories = [mount.name for mount in mounts], [mount.memory_id for mount in mounts]
    if len(set(names)) != len(names) or len(set(memories)) != len(memories):
        raise ValueError("Memory mount names and memories must be unique")
    return mounts


MemoryMounts = Annotated[tuple[MemoryMount, ...], Field(max_length=MAX_MOUNTS), AfterValidator(_unique)]
