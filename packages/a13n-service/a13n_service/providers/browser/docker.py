"""One retained Docker container per Browser; adapter shutdown leaves it running."""

import asyncio
import base64
import io
import json
import tarfile
from pathlib import Path
from typing import Literal

from docker import DockerClient
from docker.errors import ImageNotFound, NotFound
from pydantic import BaseModel, ConfigDict, Field

from a13n_service.providers.browser.definition import BrowserProviderError, LiveBrowser
from a13n_service.providers.browser.selfhost import Allocation, SelfHosted, blocking, desktop_result

LABEL = "dev.a13n.browser"
ROOT = "/home/browser/data"


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    image: str = Field(default="a13n-browser:local", min_length=1, max_length=512)
    pull_policy: Literal["never", "if_missing"] = "never"


class Docker(SelfHosted):
    def __init__(self, root: Path, client: DockerClient, config: Configuration):
        super().__init__(root, "docker")
        self.client, self.config = client, config

    async def container(self, allocation: Allocation):
        daemon = (await blocking(self.client.info))["ID"]
        if allocation.record.get("daemon", daemon) != daemon:
            raise BrowserProviderError("docker_engine_changed", certainty="known")
        identity = allocation.record.get("container_id")
        try:
            container = await blocking(self.client.containers.get, identity or f"a13n-{allocation.id}")
        except NotFound:
            return None
        if container.labels.get(LABEL) != allocation.id:
            raise BrowserProviderError("docker_identity_changed", certainty="known")
        if identity is not None and container.id != identity:
            raise BrowserProviderError("docker_identity_changed", certainty="known")
        return container

    async def provision(self, allocation: Allocation) -> None:
        allocation.record["daemon"] = (await blocking(self.client.info))["ID"]
        await allocation.save()
        container = await self.container(allocation)
        if container is None:
            try:
                image = await blocking(self.client.images.get, self.config.image)
            except ImageNotFound:
                if self.config.pull_policy == "never":
                    raise BrowserProviderError("browser_image_missing", certainty="not_dispatched") from None
                image = await blocking(self.client.images.pull, self.config.image)
            recipe = allocation.record["recipe"]
            container = await blocking(
                self.client.containers.create,
                image.id,
                name=f"a13n-{allocation.id}",
                labels={LABEL: allocation.id},
                command=["--width", str(recipe["width"]), "--height", str(recipe["height"])],
                ports={"9222/tcp": ("127.0.0.1", None), "6081/tcp": ("127.0.0.1", None)},
                shm_size="512m",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                detach=True,
            )
        allocation.record["container_id"] = container.id
        await allocation.save()

    async def launch(self, allocation: Allocation) -> LiveBrowser:
        container = await self.container(allocation)
        if container is None:
            raise BrowserProviderError("docker_container_missing", certainty="known")
        if container.status != "running":
            await blocking(container.start)
        async with asyncio.timeout(40):
            while True:
                try:
                    result = await self.observe(allocation)
                    if result is not None and result.status == "ready":
                        return result
                except BrowserProviderError as error:
                    if error.code != "docker_start_pending":
                        raise
                await asyncio.sleep(0.1)

    async def observe(self, allocation: Allocation) -> LiveBrowser | None:
        container = await self.container(allocation)
        if container is None:
            return None
        state = {
            "allocation_id": allocation.id,
            "container_id": container.id,
            "runtime_id": allocation.record.get("runtime_id"),
        }
        if container.status in {"created", "exited", "dead"}:
            return LiveBrowser(state, status="stopped", runtime_id=state["runtime_id"])
        if container.status != "running":
            raise BrowserProviderError("docker_runtime_unavailable")
        result = await blocking(
            container.exec_run,
            [
                "python3",
                "-I",
                "-c",
                "import json,pathlib; "
                f"p=pathlib.Path('{ROOT}/runtime.json'); d=json.loads(p.read_text()); "
                "s=pathlib.Path('/proc/'+str(d['pid'])+'/stat').read_text().rsplit(')',1)[1].split(); "
                "d['ready']=d['ready'] and s[19]==d['started'] and s[0]!='Z' "
                "and pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==d['boot']; "
                "print(json.dumps(d))",
            ],
        )
        if result.exit_code != 0:
            raise BrowserProviderError("docker_start_pending")
        info = json.loads(result.output)
        # The process nonce changes on container/host restart, unlike the retained container ID.
        if not info["ready"]:
            raise BrowserProviderError("docker_start_pending")
        ports = container.attrs["NetworkSettings"]["Ports"]
        state.update(
            runtime_id=info["runtime_id"],
            cdp_url=f"ws://127.0.0.1:{ports['9222/tcp'][0]['HostPort']}{info['cdp_path']}",
            viewer_url=f"ws://127.0.0.1:{ports['6081/tcp'][0]['HostPort']}",
            viewer_generation=info["viewer_generation"],
        )
        if allocation.record.get("runtime_id") != info["runtime_id"]:
            allocation.record["runtime_id"] = info["runtime_id"]
            await allocation.save()
        return LiveBrowser(state, runtime_id=info["runtime_id"])

    async def halt(self, allocation: Allocation) -> None:
        container = await self.container(allocation)
        if container is not None and container.status not in {"created", "exited", "dead"}:
            await blocking(container.stop, timeout=30)

    async def destroy(self, allocation: Allocation) -> None:
        container = await self.container(allocation)
        if container is not None:
            await blocking(container.remove, force=True, v=True)

    def download_directory(self, allocation: Allocation) -> str:
        return f"{ROOT}/downloads"

    async def write_upload(self, allocation: Allocation, identity: str, name: str, content: bytes) -> str:
        container = await self.container(allocation)
        if container is None:
            raise BrowserProviderError("docker_container_missing", certainty="known")

        def put() -> None:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                directory = tarfile.TarInfo(identity)
                directory.type, directory.mode, directory.uid, directory.gid = tarfile.DIRTYPE, 0o700, 1000, 1000
                archive.addfile(directory)
                item = tarfile.TarInfo(f"{identity}/{name}")
                item.size, item.mode, item.uid, item.gid = len(content), 0o600, 1000, 1000
                archive.addfile(item, io.BytesIO(content))
            container.put_archive(f"{ROOT}/uploads", buffer.getvalue())

        await blocking(put)
        return f"{ROOT}/uploads/{identity}/{name}"

    async def read_download(self, allocation: Allocation, guid: str, max_bytes: int) -> bytes:
        container = await self.container(allocation)
        if container is None:
            raise BrowserProviderError("docker_container_missing", certainty="known")

        def read() -> bytes:
            chunks, metadata = container.get_archive(f"{ROOT}/downloads/{guid}", chunk_size=65536)
            try:
                if metadata["size"] > max_bytes or metadata.get("linkTarget"):
                    raise BrowserProviderError("download_size_or_kind", certainty="not_dispatched")
                buffer = bytearray()
                for chunk in chunks:
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes + 65536:
                        raise BrowserProviderError("download_too_large", certainty="not_dispatched")
                with tarfile.open(fileobj=io.BytesIO(buffer)) as archive:
                    member = archive.next()
                    if member is None or not member.isfile() or member.name != guid or member.size > max_bytes:
                        raise BrowserProviderError("download_size_or_kind", certainty="not_dispatched")
                    file = archive.extractfile(member)
                    assert file is not None
                    return file.read(max_bytes + 1)
            finally:
                chunks.close()

        return await blocking(read)

    async def desktop(self, allocation: Allocation, request: dict) -> dict:
        container = await self.container(allocation)
        if container is None:
            raise BrowserProviderError("docker_container_missing", certainty="known")
        result = await blocking(
            container.exec_run,
            [
                "python3",
                "-I",
                "/opt/a13n-browser/desktop_guest.py",
                "call",
                "--root",
                f"{ROOT}/desktop",
                "--request",
                base64.b64encode(json.dumps(request).encode()).decode(),
            ],
        )
        if result.exit_code != 0:
            raise BrowserProviderError("viewer_operation_failed")
        return desktop_result(json.loads(result.output))

    async def close(self) -> None:
        await blocking(self.client.close)
