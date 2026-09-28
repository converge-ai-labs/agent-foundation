"""The assembled runtime as tenancy operations use it; resources extend this protocol."""

from typing import Protocol

from redis.asyncio import Redis

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.settings import Settings
from a13n_service.tenancy.access import Access
from a13n_service.tenancy.workspaces import WorkspaceCreated


class Runtime(Protocol):
    @property
    def storage(self) -> Storage: ...

    @property
    def objects(self) -> ObjectStore: ...

    @property
    def redis(self) -> Redis: ...

    @property
    def keys(self) -> KeyRing: ...

    @property
    def settings(self) -> Settings: ...

    @property
    def access(self) -> Access: ...

    @property
    def workspace_created(self) -> WorkspaceCreated | None: ...
