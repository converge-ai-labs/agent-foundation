"""Typed, allowlisted file operations at the Control relay boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, assert_never

from a13n_environment.files import FileOperator, FileQueryRequest, FileTextSearchRequest, FileWriteMode
from a13n_environment.models import EnvironmentAction, EnvironmentError
from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from ..domain import DomainModel
from .relay_protocol import RelayRequest
from .relay_transfers import FileTransferPlan, ReadBytes, WriteBytes


class ReadText(DomainModel):
    operation: Literal["file.read_text"] = "file.read_text"
    path: str
    line_offset: int = Field(default=0, ge=0)
    line_limit: int = Field(default=200, gt=0)
    max_line_length: int = Field(default=2_000, gt=0)


class WriteText(DomainModel):
    operation: Literal["file.write_text"] = "file.write_text"
    path: str
    text: str = Field(repr=False)
    mode: FileWriteMode


class PatchText(DomainModel):
    operation: Literal["file.patch_text"] = "file.patch_text"
    path: str
    patch: str = Field(repr=False)


class Stat(DomainModel):
    operation: Literal["file.stat"] = "file.stat"
    path: str


class ListFiles(DomainModel):
    operation: Literal["file.list"] = "file.list"
    path: str
    offset: int = Field(default=0, ge=0)
    max_results: int = Field(gt=0)
    include_hidden: bool = False


class Query(DomainModel):
    operation: Literal["file.query"] = "file.query"
    request: FileQueryRequest


class SearchText(DomainModel):
    operation: Literal["file.search_text"] = "file.search_text"
    request: FileTextSearchRequest


class Mkdir(DomainModel):
    operation: Literal["file.mkdir"] = "file.mkdir"
    path: str
    parents: bool = False
    exist_ok: bool = False


class Move(DomainModel):
    operation: Literal["file.move"] = "file.move"
    source: str
    destination: str
    replace: bool = False


class Remove(DomainModel):
    operation: Literal["file.remove"] = "file.remove"
    path: str
    recursive: bool = False


class Copy(DomainModel):
    operation: Literal["file.copy"] = "file.copy"
    source: str
    destination: str
    replace: bool = False


type UnaryFileRequest = (
    ReadText | WriteText | PatchText | Stat | ListFiles | Query | SearchText | Mkdir | Move | Remove | Copy
)
type FileRequest = Annotated[UnaryFileRequest | ReadBytes | WriteBytes, Field(discriminator="operation")]
FILE_REQUEST = TypeAdapter[FileRequest](FileRequest)
_JSON = TypeAdapter(JsonValue)


class FileRelayDispatch:
    """Bind validated file calls to one already-authorized Environment use.

    Preparation is synchronous and performs no provider I/O. The consumer must
    retain dispatch evidence and check current use authority before invoking the
    returned operation. Permissions come from the admitted use's access policy.
    """

    def __init__(self, files: FileOperator, permissions: frozenset[EnvironmentAction]) -> None:
        self._files = files
        self._permissions = permissions

    def prepare(self, message: RelayRequest) -> Callable[[], Awaitable[JsonValue]] | FileTransferPlan:
        operation, payload = message.operation, message.payload
        if "operation" in payload:
            raise ValueError("File relay payload cannot override its operation")
        request = FILE_REQUEST.validate_python({"operation": operation, **payload})
        if isinstance(request, Copy):
            required = {EnvironmentAction.FILE_COPY_SOURCE, EnvironmentAction.FILE_COPY_DESTINATION}
        else:
            required = {EnvironmentAction(f"environment.{request.operation}")}
        if not required <= self._permissions:
            raise EnvironmentError("File operation exceeds the admitted access policy", code="environment_forbidden")

        if isinstance(request, ReadBytes | WriteBytes):
            return FileTransferPlan(self._files, request)

        async def execute() -> JsonValue:
            result = await self._execute(request)
            return _JSON.validate_python(result.model_dump(mode="json"))

        return execute

    async def _execute(self, request: UnaryFileRequest) -> BaseModel:
        files = self._files
        match request:
            case ReadText():
                return await files.read_text(
                    request.path,
                    line_offset=request.line_offset,
                    line_limit=request.line_limit,
                    max_line_length=request.max_line_length,
                )
            case WriteText():
                return await files.write_text(request.path, request.text, mode=request.mode)
            case PatchText():
                return await files.patch_text(request.path, request.patch)
            case Stat():
                return await files.stat(request.path)
            case ListFiles():
                return await files.list(
                    request.path,
                    offset=request.offset,
                    max_results=request.max_results,
                    include_hidden=request.include_hidden,
                )
            case Query():
                return await files.query(request.request)
            case SearchText():
                return await files.search_text(request.request)
            case Mkdir():
                return await files.mkdir(request.path, parents=request.parents, exist_ok=request.exist_ok)
            case Move():
                return await files.move(request.source, request.destination, replace=request.replace)
            case Remove():
                return await files.remove(request.path, recursive=request.recursive)
            case Copy():
                return await files.copy(request.source, request.destination, replace=request.replace)
            case _:
                assert_never(request)
