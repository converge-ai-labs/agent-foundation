"""Docker Engine connection owned by one native Provider runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docker import DockerClient


class DockerSDKEngine:
    def __init__(self, client: DockerClient) -> None:
        self.client = client

    @classmethod
    def connect(cls, docker_host: str, *, timeout_seconds: int = 60) -> DockerSDKEngine:
        import docker

        return cls(docker.DockerClient(base_url=docker_host, timeout=timeout_seconds))

    async def close(self) -> None:
        await asyncio.to_thread(self.client.close)


@dataclass(frozen=True)
class DockerProviderRuntime:
    engine: DockerSDKEngine

    async def close(self) -> None:
        await self.engine.close()
