"""File-only, revisioned document memory over a Host-bound Environment.

The Host supplies current authority and a write coordinator. A FileOperator is
not itself evidence of conditional publication or cross-process exclusion.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import PurePosixPath
from typing import Literal, Protocol

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.memory_documents import (
    MemoryDocumentContent,
    MemoryDocumentIndex,
    MemoryDocumentReference,
    MemoryDocumentStore,
)
from a13n_harness.providers.environment.files import FileEntriesResult
from a13n_harness.providers.environment.models import EnvironmentError

from ..documents import (
    MAX_DOCUMENT_BYTES,
    MAX_READ_BYTES,
    Document,
    DocumentChange,
    DocumentHeading,
    DocumentInput,
    DocumentMutation,
    DocumentRead,
    MemoryDocumentError,
    revise_text,
)
from .configuration import FilesystemMemoryConfiguration


class MemoryFileReader(Protocol):
    async def read_bytes(self, path: str, *, offset: int = 0, length: int | None = None) -> bytes: ...


class MemoryStoreFiles(MemoryFileReader, Protocol):
    async def list(
        self, path: str, *, offset: int = 0, max_results: int, include_hidden: bool = False
    ) -> FileEntriesResult: ...


class MemoryTransactionFiles(MemoryFileReader, Protocol):
    async def mkdir(self, path: str, *, parents: bool = False, exist_ok: bool = False) -> object: ...

    async def remove(self, path: str, *, recursive: bool = False) -> object: ...


class MemoryFileTransaction(Protocol):
    """Host-fenced file access, held until all issued I/O has settled.

    The Host serializes cooperating publication and erasure across processes.
    A transaction may stage publishes until exit; successful exit acknowledges
    durable ordered publication under all observed content preconditions.
    Reconnects preserve the selected backing identity. No SQL session is held.
    Ordinary file edits are outside this commit boundary and fail integrity checks.
    """

    @property
    def files(self) -> MemoryTransactionFiles: ...

    async def publish(self, path: str, text: str, *, expected: bytes | None) -> None: ...


class MemoryFileCoordinator(Protocol):
    def transaction(self, store_id: str, scope: str) -> AbstractAsyncContextManager[MemoryFileTransaction]: ...


class _Head(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    store_id: str
    scope: str
    id: str
    version: int = Field(ge=1)
    digest: str
    navigation_digest: str
    commit: str
    deleted: bool = False


class _Commit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    id: str
    request_digest: str
    payload_digest: str
    operation: Literal["create", "revise", "delete"]
    before: _Head | None
    after: _Head
    saved_at: datetime
    principal: str
    diff: str
    unchanged: bool = False


class DocumentNavigation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    version: int
    digest: str
    path: str
    title: str
    description: str

    @classmethod
    def from_document(cls, document: Document) -> DocumentNavigation:
        return cls(
            id=document.id,
            version=document.version,
            digest=document.digest,
            path=document.path,
            title=document.title,
            description=document.description,
        )


SourceAuthorizer = Callable[[tuple[str, ...]], Awaitable[None]]
AuthorityCheck = Callable[[bool], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class FilesystemMemoryBinding:
    files: MemoryStoreFiles
    scope: str
    store_id: str
    principal: str
    authorize: AuthorityCheck
    authorize_sources: SourceAuthorizer
    coordinator: MemoryFileCoordinator | None = None
    environment_id: str | None = None
    root: str = "/memory"


_ID = re.compile(r"mdoc_[A-Za-z0-9_-]{16,64}\Z")
_COMMIT = re.compile(r"mchg_[a-f0-9]{64}\Z")
_MAX_ENVELOPE_BYTES = 2 * MAX_DOCUMENT_BYTES


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class FilesystemMemoryStore(MemoryDocumentStore):
    """A single authorized subject. Paths are never Worker-local filesystem paths."""

    supports_revisions = True
    supports_changes = False
    index_name: Literal["MEMORY.md", "_index.md"] = "_index.md"

    def __init__(
        self,
        *,
        files: MemoryStoreFiles,
        root: str,
        scope: str,
        store_id: str,
        principal: str,
        authorize: AuthorityCheck,
        authorize_sources: SourceAuthorizer,
        coordinator: MemoryFileCoordinator | None = None,
    ) -> None:
        path = PurePosixPath(root)
        if not path.is_absolute() or str(path) != root or ".." in path.parts or "\\" in root:
            raise ValueError("Memory root must be a normalized absolute Environment path")
        if any(not value.strip() or len(value) > 512 for value in (scope, store_id, principal)):
            raise ValueError("Memory requires Host-owned scope, store identity, and principal")
        self.files, self.root, self.scope, self.store_id = files, root, scope, store_id
        self.principal, self.authorize, self.authorize_sources = principal, authorize, authorize_sources
        self.coordinator = coordinator
        self.organization_fence: tuple[bytes | None] | None = None
        self.subject_root = str(path / ("mscope_" + _hash(scope.encode())))

    def _path(self, *parts: str) -> str:
        return str(PurePosixPath(self.subject_root).joinpath(*parts))

    async def _bytes(self, files: MemoryFileReader, path: str) -> bytes | None:
        try:
            result = await files.read_bytes(path, length=_MAX_ENVELOPE_BYTES + 1)
        except EnvironmentError as error:
            if error.code == "environment_not_found":
                return None
            raise MemoryDocumentError("memory_unavailable") from error
        if len(result) > _MAX_ENVELOPE_BYTES:
            raise MemoryDocumentError("memory_document_invalid")
        return result

    async def _marker(self, files: MemoryFileReader) -> None:
        raw = await self._bytes(files, self._path(".internal", "store.json"))
        expected = _json({"schema_version": 1, "store_id": self.store_id, "scope": self.scope})
        if raw != expected:
            raise MemoryDocumentError("memory_storage_unavailable")

    def _coordinator(self) -> MemoryFileCoordinator:
        if self.coordinator is None:
            raise MemoryDocumentError("memory_write_unsupported")
        return self.coordinator

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[MemoryFileTransaction]:
        coordinator = self._coordinator()
        try:
            async with coordinator.transaction(self.store_id, self.scope) as tx:
                if self.organization_fence is not None:
                    observed = await self._bytes(tx.files, self._path(".internal", "erasure.json"))
                    if observed != self.organization_fence[0]:
                        raise MemoryDocumentError("memory_organization_revoked")
                yield tx
        except (MemoryDocumentError, asyncio.CancelledError):
            raise
        except Exception as error:
            raise MemoryDocumentError("memory_write_unconfirmed") from error

    async def initialize(self) -> None:
        """Explicit initial creation. Ordinary reads/reconnects never recreate a marker."""
        await self.authorize(True)
        async with self._transaction() as tx:
            marker = self._path(".internal", "store.json")
            previous = await self._bytes(tx.files, marker)
            if previous is not None:
                await self._marker(tx.files)
                return
            # The Host must establish that this is a new binding before calling initialize.
            for directory in ("heads", "revisions", "commits", "requests", "staging", "indexes"):
                await tx.files.mkdir(self._path(".internal", directory), parents=True, exist_ok=True)
            for kind in ("semantic", "procedural", "episodic"):
                await tx.files.mkdir(self._path(kind), parents=True, exist_ok=True)
            await tx.publish(
                marker,
                _json({"schema_version": 1, "store_id": self.store_id, "scope": self.scope}).decode(),
                expected=None,
            )

    async def _head(self, files: MemoryFileReader, document_id: str, *, deleted: bool = False) -> tuple[_Head, bytes]:
        if not _ID.fullmatch(document_id):
            raise MemoryDocumentError("memory_reference_invalid")
        raw = await self._bytes(files, self._path(".internal", "heads", document_id + ".json"))
        if raw is None:
            raise MemoryDocumentError("memory_not_found")
        head = _Head.model_validate_json(raw)
        if (
            head.id != document_id
            or head.scope != self.scope
            or head.store_id != self.store_id
            or not _COMMIT.fullmatch(head.commit)
        ):
            raise MemoryDocumentError("memory_document_invalid")
        if head.deleted and not deleted:
            raise MemoryDocumentError("memory_not_found")
        return head, raw

    async def _document(self, files: MemoryFileReader, head: _Head) -> Document:
        raw = await self._bytes(files, self._path(".internal", "revisions", head.id, head.commit + ".md"))
        commit_raw = await self._bytes(files, self._path(".internal", "commits", head.id, head.commit + ".json"))
        if raw is None or commit_raw is None:
            raise MemoryDocumentError("memory_unavailable")
        document = self._decode_document(raw)
        commit = _Commit.model_validate_json(commit_raw)
        if (
            document.id != head.id
            or document.version != head.version
            or document.digest != head.digest
            or document.store_id != self.store_id
            or document.scope != self.scope
            or commit.after != head
        ):
            raise MemoryDocumentError("memory_document_changed")
        await self.authorize_sources(document.sources)
        return document

    async def document(self, document_id: str, *, version: int | None = None) -> Document:
        await self.authorize(False)
        await self._marker(self.files)
        head, raw = await self._head(self.files, document_id)
        if version is not None and version != head.version:
            history = await self._history(self.files, head)
            found = next((entry for entry in history if entry.version == version), None)
            if found is None:
                raise MemoryDocumentError("memory_not_found")
            selected = found
        else:
            selected = head
        document = await self._document(self.files, selected)
        _, current = await self._head(self.files, document_id)
        if current != raw:
            raise MemoryDocumentError("memory_conflict")
        await self.authorize(False)
        return document

    async def _history(self, files: MemoryFileReader, head: _Head) -> list[_Head]:
        result = []
        while True:
            result.append(head)
            if len(result) > 1000:
                raise MemoryDocumentError("memory_history_limit")
            raw = await self._bytes(files, self._path(".internal", "commits", head.id, head.commit + ".json"))
            if raw is None:
                raise MemoryDocumentError("memory_unavailable")
            commit = _Commit.model_validate_json(raw)
            if commit.after != head:
                raise MemoryDocumentError("memory_document_changed")
            if commit.before is None:
                return result
            if commit.before.id != head.id or commit.before.version != head.version - 1:
                raise MemoryDocumentError("memory_document_invalid")
            head = commit.before

    async def history(
        self, document_id: str, *, before_version: int | None = None, limit: int = 20
    ) -> tuple[Document, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("History limit must be between 1 and 100")
        await self.authorize(False)
        await self._marker(self.files)
        head, _ = await self._head(self.files, document_id)
        entries = [
            item
            for item in await self._history(self.files, head)
            if before_version is None or item.version < before_version
        ]
        return tuple([await self.document(document_id, version=item.version) for item in entries[:limit]])

    async def _save(
        self,
        tx: MemoryFileTransaction,
        document: Document,
        *,
        previous: Document | None,
        previous_head: _Head | None,
        expected: bytes | None,
        request_key: str,
        payload_digest: str,
    ) -> DocumentMutation:
        request_digest = _hash(_json([self.store_id, self.scope, request_key]))
        change_id = "mchg_" + request_digest
        revision_path = self._path(".internal", "revisions", document.id, change_id + ".md")
        staged = await self._bytes(tx.files, revision_path)
        if staged is not None:
            prepared = self._decode_document(staged)
            if prepared.model_dump(exclude={"created_at", "saved_at"}) != document.model_dump(
                exclude={"created_at", "saved_at"}
            ):
                raise MemoryDocumentError("memory_conflict")
            document = prepared
        else:
            intent = await self._bytes(tx.files, self._path(".internal", "requests", request_digest + ".json"))
            if intent is not None:
                accepted = _Commit.model_validate_json(intent)
                if accepted.payload_digest != payload_digest or accepted.after.id != document.id:
                    raise MemoryDocumentError("memory_conflict")
                document = document.model_copy(
                    update={
                        "saved_at": accepted.saved_at,
                        "created_at": previous.created_at if previous is not None else accepted.saved_at,
                        "principal": accepted.principal,
                    }
                )
        head = _Head(
            store_id=self.store_id,
            scope=self.scope,
            id=document.id,
            version=document.version,
            digest=document.digest,
            navigation_digest=_hash(DocumentNavigation.from_document(document).model_dump_json().encode()),
            commit=change_id,
        )
        # Full revisions are authoritative; the diff is inspection data only.
        before_lines = previous.text.splitlines(keepends=True) if previous else []
        after_lines = document.text.splitlines(keepends=True)
        if len(before_lines) + len(after_lines) > 10000:
            raise MemoryDocumentError("memory_change_too_large")
        diff = "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in unified_diff(
                before_lines,
                after_lines,
                fromfile="before",
                tofile="after",
            )
        )
        if len(diff.encode()) > MAX_DOCUMENT_BYTES * 2:
            raise MemoryDocumentError("memory_change_too_large")
        commit = _Commit(
            id=change_id,
            request_digest=request_digest,
            payload_digest=payload_digest,
            operation="revise" if previous else "create",
            before=previous_head,
            after=head,
            saved_at=document.saved_at,
            principal=self.principal,
            diff=diff,
        )
        # A receipt is intent until its exact head appears in the committed ancestry.
        # It contains no body or diff, so erasure retains only operation evidence.
        await self._immutable(
            tx,
            self._path(".internal", "requests", request_digest + ".json"),
            commit.model_copy(update={"diff": ""}).model_dump_json(),
        )
        for directory in ("revisions", "commits"):
            await tx.files.mkdir(self._path(".internal", directory, document.id), parents=True, exist_ok=True)
        # A failed/stale writer can leave staged immutable records, but cannot expose a head.
        await self._immutable(tx, revision_path, self._encode_document(document))
        await self._immutable(
            tx, self._path(".internal", "commits", document.id, change_id + ".json"), commit.model_dump_json()
        )
        await tx.publish(
            self._path(".internal", "heads", document.id + ".json"), head.model_dump_json(), expected=expected
        )
        verified = await self._document(tx.files, head)
        if verified != document:
            raise MemoryDocumentError("memory_write_unconfirmed")
        indexed = False
        try:
            await tx.files.mkdir(self._path(".internal", "indexes", document.id), parents=True, exist_ok=True)
            await self._immutable(
                tx,
                self._path(".internal", "indexes", document.id, change_id + ".json"),
                DocumentNavigation.from_document(document).model_dump_json(),
            )
            indexed = True
        except asyncio.CancelledError:
            raise
        except Exception:
            # The document is already committed. A cache failure cannot turn it
            # into a failed creation that invites another write.
            pass
        return DocumentMutation(document=document, change_id=change_id, indexed=indexed)

    @staticmethod
    def _encode_document(document: Document) -> str:
        # JSON is a YAML subset. The single-line frontmatter envelope cannot
        # reinterpret user body fields as identity or authority.
        metadata = document.model_dump(mode="json", exclude={"text"})
        return "---\n" + _json(metadata).decode() + "\n---\n" + document.text

    @staticmethod
    def _decode_document(raw: bytes) -> Document:
        try:
            envelope, body = raw.decode().split("\n---\n", 1)
            if not envelope.startswith("---\n"):
                raise ValueError("Missing metadata")
            metadata = json.loads(envelope[4:])
            return Document.model_validate({**metadata, "text": body})
        except (ValueError, TypeError) as error:
            raise MemoryDocumentError("memory_document_invalid") from error

    async def _result(
        self, files: MemoryFileReader, document: Document, change_id: str, *, unchanged: bool = False
    ) -> DocumentMutation:
        cached = await self._bytes(files, self._path(".internal", "indexes", document.id, change_id + ".json"))
        indexed = cached == DocumentNavigation.from_document(document).model_dump_json().encode()
        return DocumentMutation(
            document=document, change_id=None if unchanged else change_id, unchanged=unchanged, indexed=indexed
        )

    async def _immutable(self, tx: MemoryFileTransaction, path: str, text: str) -> None:
        existing = await self._bytes(tx.files, path)
        if existing is not None:
            if existing != text.encode():
                raise MemoryDocumentError("memory_conflict")
            return
        await tx.publish(path, text, expected=None)

    async def create_document(self, value: DocumentInput, *, request_key: str) -> DocumentMutation:
        await self.authorize(True)
        await self.authorize_sources(value.sources)
        request = self._request(request_key)
        document_id = "mdoc_" + request[:32]
        payload = _hash(_json(value.model_dump(mode="json")))
        if PurePosixPath(value.path).parts[0] != value.kind:
            raise MemoryDocumentError("memory_document_invalid")
        async with self._transaction() as tx:
            await self._marker(tx.files)
            await self._request_commit(tx.files, request, payload)
            try:
                head, _ = await self._head(tx.files, document_id, deleted=True)
            except MemoryDocumentError as error:
                if error.code != "memory_not_found":
                    raise
            else:
                if head.deleted:
                    raise MemoryDocumentError("memory_not_found")
                commit = await self._request_commit(tx.files, request, payload)
                if commit is None:
                    # Publication may have succeeded before the request receipt was saved.
                    raw = await self._bytes(
                        tx.files, self._path(".internal", "commits", head.id, head.commit + ".json")
                    )
                    if raw is None:
                        raise MemoryDocumentError("memory_write_unconfirmed")
                    commit = _Commit.model_validate_json(raw)
                    if commit.request_digest != request or commit.payload_digest != payload:
                        raise MemoryDocumentError("memory_conflict")
                return await self._result(tx.files, await self._document(tx.files, commit.after), commit.id)
            now = datetime.now(UTC)
            document = Document(
                **value.model_dump(),
                id=document_id,
                version=1,
                scope=self.scope,
                store_id=self.store_id,
                principal=self.principal,
                created_at=now,
                saved_at=now,
            )
            if len(document.model_dump_json().encode()) > MAX_DOCUMENT_BYTES:
                raise MemoryDocumentError("memory_document_too_large")
            return await self._save(
                tx,
                document,
                previous=None,
                previous_head=None,
                expected=None,
                request_key=request_key,
                payload_digest=payload,
            )

    def _request(self, key: str) -> str:
        if not key or len(key) > 512:
            raise MemoryDocumentError("memory_request_invalid")
        return _hash(_json([self.store_id, self.scope, key]))

    async def _request_commit(self, files: MemoryFileReader, request: str, payload: str) -> _Commit | None:
        raw = await self._bytes(files, self._path(".internal", "requests", request + ".json"))
        if raw is None:
            return None
        commit = _Commit.model_validate_json(raw)
        if commit.request_digest != request or commit.payload_digest != payload:
            raise MemoryDocumentError("memory_conflict")
        try:
            head, _ = await self._head(files, commit.after.id, deleted=True)
        except MemoryDocumentError as error:
            if error.code == "memory_not_found":
                return None
            raise
        if head.deleted:
            raise MemoryDocumentError("memory_not_found")
        history = await self._history(files, head)
        return commit if commit.after in history else None

    async def revise(
        self,
        document_id: str,
        *,
        expected_version: int,
        change: DocumentChange,
        request_key: str,
        sources: tuple[str, ...] | None = None,
    ) -> DocumentMutation:
        await self.authorize(True)
        request = self._request(request_key)
        payload_values: list[object] = [document_id, expected_version, change.model_dump(mode="json")]
        if sources is not None:
            await self.authorize_sources(sources)
            payload_values.append(sources)
        payload = _hash(_json(payload_values))
        async with self._transaction() as tx:
            await self._marker(tx.files)
            head, raw = await self._head(tx.files, document_id)
            completed = await self._request_commit(tx.files, request, payload)
            if completed is not None:
                if completed.after.id != document_id:
                    raise MemoryDocumentError("memory_conflict")
                return await self._result(
                    tx.files,
                    await self._document(tx.files, completed.after),
                    completed.id,
                    unchanged=completed.unchanged,
                )
            if head.commit == "mchg_" + request:
                commit_raw = await self._bytes(
                    tx.files, self._path(".internal", "commits", head.id, head.commit + ".json")
                )
                if commit_raw is None or _Commit.model_validate_json(commit_raw).payload_digest != payload:
                    raise MemoryDocumentError("memory_conflict")
                return await self._result(tx.files, await self._document(tx.files, head), head.commit)
            if head.version != expected_version:
                raise MemoryDocumentError("memory_conflict")
            previous = await self._document(tx.files, head)
            if previous.kind == "episodic":
                raise MemoryDocumentError("memory_revision_unsupported")
            text = revise_text(previous.text, change)
            if text == previous.text and (sources is None or sources == previous.sources):
                receipt = _Commit(
                    id=head.commit,
                    request_digest=request,
                    payload_digest=payload,
                    operation="revise",
                    before=head,
                    after=head,
                    saved_at=previous.saved_at,
                    principal=self.principal,
                    diff="",
                    unchanged=True,
                )
                await self._immutable(
                    tx, self._path(".internal", "requests", request + ".json"), receipt.model_dump_json()
                )
                return await self._result(tx.files, previous, head.commit, unchanged=True)
            if previous.version >= 1000:
                raise MemoryDocumentError("memory_history_limit")
            document = Document.model_validate(
                {
                    **previous.model_dump(),
                    "text": text,
                    "sources": previous.sources if sources is None else sources,
                    "version": previous.version + 1,
                    "saved_at": datetime.now(UTC),
                    "principal": self.principal,
                }
            )
            if len(document.model_dump_json().encode()) > MAX_DOCUMENT_BYTES:
                raise MemoryDocumentError("memory_document_too_large")
            return await self._save(
                tx,
                document,
                previous=previous,
                previous_head=head,
                expected=raw,
                request_key=request_key,
                payload_digest=payload,
            )

    async def _documents(self) -> list[Document]:
        await self.authorize(False)
        await self._marker(self.files)
        listing = await self.files.list(self._path(".internal", "heads"), max_results=1001)
        if listing.has_more or len(listing.entries) > 1000:
            raise MemoryDocumentError("memory_retrieval_limit")
        result = []
        total_bytes = 0
        for item in listing.entries:
            document_id = PurePosixPath(item.path).stem
            if item.kind != "file" or not _ID.fullmatch(document_id):
                raise MemoryDocumentError("memory_document_invalid")
            head, _ = await self._head(self.files, document_id, deleted=True)
            if not head.deleted:
                document = await self.document(document_id)
                total_bytes += len(document.model_dump_json().encode())
                if total_bytes > 8 * 1024 * 1024:
                    raise MemoryDocumentError("memory_retrieval_limit")
                result.append(document)
        return sorted(result, key=lambda item: (item.path, item.id))

    async def _navigation(self) -> list[DocumentNavigation]:
        await self.authorize(False)
        await self._marker(self.files)
        listing = await self.files.list(self._path(".internal", "heads"), max_results=1001)
        if listing.has_more or len(listing.entries) > 1000:
            raise MemoryDocumentError("memory_retrieval_limit")
        items = []
        for entry in listing.entries:
            document_id = PurePosixPath(entry.path).stem
            head, raw = await self._head(self.files, document_id, deleted=True)
            if head.deleted:
                continue
            cached = await self._bytes(
                self.files, self._path(".internal", "indexes", document_id, head.commit + ".json")
            )
            metadata = (
                DocumentNavigation.model_validate_json(cached)
                if cached is not None and _hash(cached) == head.navigation_digest
                else None
            )
            if metadata is None or (metadata.id, metadata.version, metadata.digest) != (
                head.id,
                head.version,
                head.digest,
            ):
                metadata = DocumentNavigation.from_document(await self.document(document_id))
            _, current = await self._head(self.files, document_id)
            if raw != current:
                raise MemoryDocumentError("memory_conflict")
            items.append(metadata)
        await self.authorize(False)
        return sorted(items, key=lambda item: (item.path, item.id))

    async def organization_token(self) -> str | None:
        await self.authorize(False)
        await self._marker(self.files)
        raw = await self._bytes(self.files, self._path(".internal", "erasure.json"))
        return raw.decode() if raw is not None else None

    async def organization_plan(self, work_id: str, *, text: str | None = None) -> str | None:
        if re.fullmatch(r"morg_[a-f0-9]{64}", work_id) is None:
            raise MemoryDocumentError("memory_query_invalid")
        await self.authorize(text is not None)
        path = self._path(".internal", "organization", work_id + ".json")
        if text is None:
            await self._marker(self.files)
            raw = await self._bytes(self.files, path)
            return raw.decode() if raw is not None else None
        if len(text.encode()) > MAX_DOCUMENT_BYTES:
            raise MemoryDocumentError("memory_document_too_large")
        async with self._transaction() as tx:
            await self._marker(tx.files)
            existing = await self._bytes(tx.files, path)
            if existing is not None:
                if existing.decode() != text:
                    raise MemoryDocumentError("memory_conflict")
                return text
            await tx.files.mkdir(self._path(".internal", "organization"), parents=True, exist_ok=True)
            await tx.publish(path, text, expected=None)
        return text

    async def list_documents(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[DocumentNavigation, ...], str | None]:
        if not 1 <= limit <= 100:
            raise MemoryDocumentError("memory_query_invalid")
        items = await self._navigation()
        identity = _hash(_json([self.store_id, self.scope, [(item.id, item.version) for item in items]]))
        offset = 0
        if cursor:
            match = re.fullmatch(r"([a-f0-9]{64}):(\d{1,4})", cursor)
            if not match or match[1] != identity:
                raise MemoryDocumentError("memory_cursor_invalid")
            offset = int(match[2])
        selected = tuple(items[offset : offset + limit])
        end = offset + len(selected)
        return selected, f"{identity}:{end}" if end < len(items) else None

    async def index(self, *, cursor: str | None = None) -> MemoryDocumentIndex:
        documents = await self._navigation()
        identity = _hash(_json([(item.id, item.version) for item in documents]))
        offset = 0
        if cursor:
            match = re.fullmatch(r"([a-f0-9]{64}):(\d{1,4})", cursor)
            if not match or match[1] != identity:
                raise MemoryDocumentError("memory_cursor_invalid")
            offset = int(match[2])
        lines = ["# _index.md\n"]
        end = offset
        for document in documents[offset:]:
            line = json.dumps(
                {
                    "reference": f"memory://{document.id}",
                    "version": document.version,
                    "path": document.path,
                    "title": document.title,
                    "description": document.description,
                },
                ensure_ascii=False,
            )
            candidate = "\n".join([*lines, line])
            # Leave room for escaping and the untrusted context envelope.
            if len(json.dumps(candidate, ensure_ascii=False).encode()) > 24 * 1024:
                break
            lines.append(line)
            end += 1
        return MemoryDocumentIndex("\n".join(lines), f"{identity}:{end}" if end < len(documents) else None)

    async def read(self, document_id: str) -> MemoryDocumentContent:
        document = await self.document(document_id)
        return MemoryDocumentContent(document.id, document.title, document.text)

    async def search(self, query: str, *, limit: int) -> tuple[MemoryDocumentReference, ...]:
        if not query.strip() or len(query) > 16000 or not 1 <= limit <= 100:
            raise MemoryDocumentError("memory_query_invalid")
        # Unicode substrings preserve Chinese and mixed-language lexical retrieval.
        terms = query.casefold().split()
        ranked = []
        for item in await self._documents():
            text = (item.title + "\n" + item.description + "\n" + item.text).casefold()
            score = sum(text.count(term) for term in terms)
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        return tuple(MemoryDocumentReference(item.id, item.title, item.description) for _, item in ranked[:limit])

    async def toc(self, document_id: str, *, version: int | None = None) -> tuple[DocumentHeading, ...]:
        document = await self.document(document_id, version=version)
        tokens = MarkdownIt().parse(document.text)
        lines = document.text.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        headings = []
        for index, token in enumerate(tokens):
            if token.type == "heading_open" and token.map is not None:
                start = offsets[token.map[0]]
                headings.append(
                    DocumentHeading(
                        title=tokens[index + 1].content,
                        level=int(token.tag[1:]),
                        locator=f"{document.version}:{document.digest}:{start}",
                        start=start,
                        end=len(document.text),
                    )
                )
        if len(headings) > 1000:
            raise MemoryDocumentError("memory_document_too_large")
        for index, heading in enumerate(headings):
            end = next(
                (later.start for later in headings[index + 1 :] if later.level <= heading.level), len(document.text)
            )
            headings[index] = heading.model_copy(update={"end": end})
        return tuple(headings)

    async def read_range(
        self,
        document_id: str,
        *,
        version: int | None = None,
        section: str | None = None,
        start: int = 0,
        length: int = MAX_READ_BYTES,
    ) -> DocumentRead:
        if start < 0 or not 1 <= length <= MAX_READ_BYTES:
            raise MemoryDocumentError("memory_range_invalid")
        document = await self.document(document_id, version=version)
        lower, upper = 0, len(document.text)
        if section is not None:
            headings = await self.toc(document_id, version=document.version)
            heading = next((heading for heading in headings if heading.locator == section), None)
            if heading is None:
                raise MemoryDocumentError("memory_section_stale")
            lower, upper = heading.start, heading.end
        position = lower + start
        chunk = document.text[position:upper].encode()[:length].decode("utf-8", errors="ignore")
        end = start + len(chunk)
        return DocumentRead(
            id=document.id,
            version=document.version,
            digest=document.digest,
            title=document.title,
            path=document.path,
            text=chunk,
            start=start,
            next_start=end if lower + end < upper else None,
        )

    async def create(
        self,
        text: str,
        *,
        title: str,
        description: str,
        kind: Literal["daily", "long_term"],
        correction_of: str | None,
        request_key: str,
    ) -> MemoryDocumentReference:
        # Legacy labels are not inferred into semantic types.
        raise MemoryDocumentError("memory_document_kind_required")

    async def delete(self, document_id: str) -> None:
        await self.authorize(True)
        async with self._transaction() as tx:
            await self._marker(tx.files)
            head, raw = await self._head(tx.files, document_id, deleted=True)
            if not head.deleted:
                await self._document(tx.files, head)
                epoch_path = self._path(".internal", "erasure.json")
                epoch = await self._bytes(tx.files, epoch_path)
                await tx.publish(
                    epoch_path, _json([document_id, datetime.now(UTC).isoformat()]).decode(), expected=epoch
                )
                await tx.publish(
                    self._path(".internal", "heads", document_id + ".json"),
                    head.model_copy(update={"deleted": True}).model_dump_json(),
                    expected=raw,
                )
            # Tombstone blocks reads and replay before physical erasure; retries finish cleanup.
            # Extraction plans can contain copied candidate text. Erasure also
            # removes those plans and fences every older organization writer.
            try:
                await tx.files.remove(self._path(".internal", "organization"), recursive=True)
            except EnvironmentError as error:
                if error.code != "environment_not_found":
                    raise MemoryDocumentError("memory_write_unconfirmed") from error
            for directory in ("revisions", "commits", "indexes"):
                try:
                    await tx.files.remove(self._path(".internal", directory, document_id), recursive=True)
                except EnvironmentError as error:
                    if error.code != "environment_not_found":
                        raise MemoryDocumentError("memory_write_unconfirmed") from error


@asynccontextmanager
async def open_filesystem_store(
    configuration: FilesystemMemoryConfiguration, *, binding: FilesystemMemoryBinding
) -> AsyncIterator[FilesystemMemoryStore]:
    """Environment acquisition and authority belong to the Host.

    Never turn an authored root into a Path on this process or acquire a second adapter.
    """
    if configuration.storage.root != binding.root or (
        configuration.storage.environment_id is not None
        and configuration.storage.environment_id != binding.environment_id
    ):
        raise MemoryDocumentError("memory_storage_binding_mismatch")
    yield FilesystemMemoryStore(
        files=binding.files,
        root=configuration.storage.root,
        scope=binding.scope,
        store_id=binding.store_id,
        principal=binding.principal,
        authorize=binding.authorize,
        authorize_sources=binding.authorize_sources,
        coordinator=binding.coordinator,
    )
