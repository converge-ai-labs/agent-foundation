"""Immutable objects scoped to an installed App and workspace."""

from dataclasses import dataclass

from a13n_service.infra.db import short_session
from a13n_service.infra.objects.interface import ObjectRef as ObjectRef
from a13n_service.infra.objects.interface import new_key, read, validate_key
from a13n_service.runs.runtime import Runtime

from ._access import app_workspace


@dataclass(frozen=True, slots=True)
class AppObjects:
    """Open a short authorization read per operation; perform object I/O after it closes."""

    runtime: Runtime
    workspace_id: str
    app_id: str

    async def _namespace(self) -> str:
        async with short_session(self.runtime.storage) as session:
            scope = await app_workspace(session, self.runtime, self.workspace_id, self.app_id, write=False)
        return validate_key(f"apps/{self.app_id}/{scope.organization_id}/{scope.workspace_id}")

    @staticmethod
    def _in_namespace(namespace: str, key: str) -> str:
        key = validate_key(key)
        if not key.startswith(f"{namespace}/"):
            raise ValueError("Object key is outside the App workspace namespace")
        return key

    async def put(self, data: bytes, *, content_type: str, prefix: str = "") -> ObjectRef:
        """Write a fresh object, optionally in a relative subdirectory."""
        namespace = await self._namespace()
        directory = f"{namespace}/{validate_key(prefix)}" if prefix else namespace
        return await self.runtime.objects.put(validate_key(new_key(directory)), data, content_type=content_type)

    async def get(self, key: str) -> bytes | None:
        """Get bytes by the full key returned by put or keys."""
        return await self.runtime.objects.get(self._in_namespace(await self._namespace(), key))

    async def read(self, ref: ObjectRef) -> bytes:
        """Read bytes and verify the committed reference."""
        self._in_namespace(await self._namespace(), ref.key)
        return await read(self.runtime.objects, ref)

    async def keys(self, prefix: str = "", *, limit: int = 100, after: str | None = None) -> list[str]:
        """List full keys under a relative subdirectory, using a full key as the cursor."""
        namespace = await self._namespace()
        directory = f"{namespace}/{validate_key(prefix)}" if prefix else namespace
        validate_key(directory)
        if after is not None:
            self._in_namespace(directory, after)
        return await self.runtime.objects.keys(directory, limit=limit, after=after)

    async def delete(self, key: str) -> None:
        await self.runtime.objects.delete(self._in_namespace(await self._namespace(), key))
