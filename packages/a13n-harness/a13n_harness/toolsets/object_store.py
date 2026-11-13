"""Agent-facing object management over Host storage and authorized Environment files."""

from __future__ import annotations

import mimetypes
from typing import TYPE_CHECKING, Annotated

from pydantic import Field
from pydantic_ai import RunContext, ToolFailed

from a13n_harness.context import AgentContext
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.objects import ObjectStore, ObjectStoreError, object_id
from a13n_harness.tools.diagnostics import system_failure, tool_error_text
from a13n_harness.toolsets._results import environment_failure

if TYPE_CHECKING:
    from a13n_harness.capabilities.object_store import ObjectStoreConfiguration


class ObjectStoreTools:
    def __init__(self, configuration: ObjectStoreConfiguration) -> None:
        self.configuration = configuration

    def _store(self, ctx: RunContext[AgentContext]) -> ObjectStore:
        store = ctx.deps.object_store
        assert store is not None
        return store

    async def asset_ref(
        self, ctx: RunContext[AgentContext], path: str, media_type: str | None = None
    ) -> dict[str, object]:
        """Snapshot an authorized Environment file into temporary LRU storage; return its exact object ref."""
        environment = ctx.deps.environment
        data = bytearray()
        try:
            selected = await environment.resolve_files(path)
            async with environment.open_files(selected) as files:
                metadata = await files.stat(selected.logical_path)
                if metadata.kind != "file":
                    raise system_failure("invalid_argument", "Only a regular file can be imported.")
                if metadata.size is not None and metadata.size > self.configuration.max_object_bytes:
                    raise system_failure("object_too_large", "File exceeds the Object Store import limit.")
                async for chunk in files.read_bytes_stream(selected.logical_path):
                    data.extend(chunk)
                    if len(data) > self.configuration.max_object_bytes:
                        raise system_failure("object_too_large", "File exceeds the Object Store import limit.")
        except EnvironmentError as exc:
            raise ToolFailed(tool_error_text(environment_failure(exc)["error"])) from exc
        try:
            info = await self._store(ctx).put(
                bytes(data),
                media_type=media_type or mimetypes.guess_type(path)[0] or "application/octet-stream",
                name=path.replace("\\", "/").rsplit("/", 1)[-1],
            )
            return info.presentation()
        except ObjectStoreError as exc:
            raise system_failure(exc.code, str(exc)) from exc

    async def asset_store(
        self, ctx: RunContext[AgentContext], ref: str | None = None, path: str | None = None
    ) -> dict[str, object]:
        """Retain an object long-term, preserving its ID; or import and retain an authorized file. Idempotent for a ref."""
        if (ref is None) == (path is None):
            raise system_failure("invalid_argument", "Supply exactly one of ref or path.")
        if path is not None:
            imported = await self.asset_ref(ctx, path)
            ref = str(imported["ref"])
        assert ref is not None
        try:
            return (await self._store(ctx).store(object_id(ref))).presentation()
        except ObjectStoreError as exc:
            raise system_failure(exc.code, str(exc)) from exc

    async def asset_info(self, ctx: RunContext[AgentContext], ref: str) -> dict[str, object]:
        """Read object metadata without returning bytes or base64. References remain Host-scoped."""
        try:
            return (await self._store(ctx).info(object_id(ref))).presentation()
        except ObjectStoreError as exc:
            raise system_failure(exc.code, str(exc)) from exc

    async def asset_list(
        self,
        ctx: RunContext[AgentContext],
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> list[dict[str, object]]:
        """List a bounded page of currently available temporary and retained objects in this Host scope."""
        return [item.presentation() for item in await self._store(ctx).list(offset=offset, limit=limit)]

    async def asset_delete(self, ctx: RunContext[AgentContext], ref: str) -> dict[str, bool]:
        """Remove an object from future lookup in this Host scope. Already-read call-local bytes remain valid."""
        try:
            await self._store(ctx).delete(object_id(ref))
            return {"deleted": True}
        except ObjectStoreError as exc:
            raise system_failure(exc.code, str(exc)) from exc
