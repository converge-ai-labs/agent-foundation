from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlparse

from ..management import EnvironmentProviderRuntime

_CONTAINER_ID = re.compile(r"^(?:sha256:)?(?P<digest>[0-9a-f]{64})$")
_BOOTSTRAP_CORRELATION = re.compile(r"^bootstrap-[0-9a-f]{24}$")
_EIP_PORT_KEY = "8787/tcp"


class DockerEngineError(RuntimeError):
    def __init__(self, message: str, *, code: str = "docker_engine_failed", dispatched: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.dispatched = dispatched


class DockerBootstrapStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DockerImageInspection:
    image_id: str
    user: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "image_id", _normalized_id(self.image_id))


@dataclass(frozen=True, slots=True)
class DockerEngineMount:
    type: Literal["bind", "volume"]
    source: str
    target: str
    read_only: bool


@dataclass(frozen=True, slots=True)
class DockerContainerSpec:
    image_id: str
    command: tuple[str, ...]
    environment: Mapping[str, str]
    labels: Mapping[str, str]
    mounts: tuple[DockerEngineMount, ...]
    eip_container_port: int
    nano_cpus: int | None
    memory_bytes: int | None
    pids_limit: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "image_id", _normalized_id(self.image_id))
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


@dataclass(frozen=True, slots=True)
class DockerContainerInspection:
    container_id: str
    image_id: str
    status: str
    user: str
    command: tuple[str, ...]
    environment: Mapping[str, str]
    labels: Mapping[str, str]
    mounts: tuple[DockerEngineMount, ...]
    eip_host_ip: str | None
    eip_host_port: int | None
    eip_binding_exact: bool
    eip_route_exact: bool
    nano_cpus: int | None
    memory_bytes: int | None
    pids_limit: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "container_id", _normalized_id(self.container_id))
        object.__setattr__(self, "image_id", _normalized_id(self.image_id))
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


class DockerEngine(ABC):
    """Typed async boundary for the Docker lifecycle operations used by this provider."""

    @abstractmethod
    async def validate_local_topology(self) -> None: ...

    @abstractmethod
    async def inspect_image(self, reference: str) -> DockerImageInspection | None: ...

    @abstractmethod
    async def pull_image(self, reference: str) -> None: ...

    @abstractmethod
    async def validate_mount(self, mount: DockerEngineMount) -> None: ...

    @abstractmethod
    async def create_container(self, spec: DockerContainerSpec) -> str: ...

    @abstractmethod
    async def inspect_container(self, container_id: str) -> DockerContainerInspection | None: ...

    @abstractmethod
    async def find_containers(self, labels: Mapping[str, str]) -> tuple[DockerContainerInspection, ...]: ...

    @abstractmethod
    async def start_container(self, container_id: str) -> None: ...

    @abstractmethod
    async def stop_container(self, container_id: str, *, timeout_seconds: int) -> None: ...

    @abstractmethod
    async def remove_container(self, container_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DockerBootstrapMaterial:
    environment_id: str
    configuration_fingerprint: str
    envd_configuration: bytes
    credential: str

    @property
    def digest(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.environment_id.encode())
        digest.update(b"\0")
        digest.update(self.configuration_fingerprint.encode())
        digest.update(b"\0")
        digest.update(self.envd_configuration)
        digest.update(b"\0")
        digest.update(self.credential.encode())
        return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True, slots=True)
class DockerBootstrapAllocation:
    correlation: str
    directory: Path
    material: DockerBootstrapMaterial


class DockerBootstrapStore(ABC):
    @abstractmethod
    async def create(
        self,
        correlation: str,
        material: DockerBootstrapMaterial,
    ) -> DockerBootstrapAllocation: ...

    @abstractmethod
    async def recover(self, correlation: str) -> DockerBootstrapAllocation | None: ...

    @abstractmethod
    async def replace(
        self,
        correlation: str,
        material: DockerBootstrapMaterial,
    ) -> DockerBootstrapAllocation: ...

    @abstractmethod
    async def remove(self, correlation: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DockerProviderRuntime(EnvironmentProviderRuntime):
    engine: DockerEngine
    bootstrap_store: DockerBootstrapStore

    def __post_init__(self) -> None:
        if not isinstance(self.engine, DockerEngine):
            raise TypeError("Docker runtime engine must implement DockerEngine")
        if not isinstance(self.bootstrap_store, DockerBootstrapStore):
            raise TypeError("Docker runtime bootstrap_store must implement DockerBootstrapStore")


class DirectoryDockerBootstrapStore(DockerBootstrapStore):
    """Simple durable bootstrap store rooted at one Host-selected directory."""

    def __init__(self, root: Path) -> None:
        expanded = root.expanduser()
        if "\x00" in str(expanded) or not expanded.is_absolute():
            raise ValueError("Docker bootstrap store root must be an absolute path without NUL")
        self._root = expanded
        self._lock = asyncio.Lock()

    async def create(
        self,
        correlation: str,
        material: DockerBootstrapMaterial,
    ) -> DockerBootstrapAllocation:
        _validate_correlation(correlation)
        async with self._lock:
            existing = await _to_thread_complete(self._recover, correlation)
            if existing is not None:
                if existing.material.digest != material.digest:
                    raise DockerBootstrapStoreError("Docker bootstrap correlation already contains different material")
                return existing
            return await _to_thread_complete(self._publish_new, correlation, material)

    async def recover(self, correlation: str) -> DockerBootstrapAllocation | None:
        _validate_correlation(correlation)
        async with self._lock:
            return await _to_thread_complete(self._recover, correlation)

    async def replace(
        self,
        correlation: str,
        material: DockerBootstrapMaterial,
    ) -> DockerBootstrapAllocation:
        _validate_correlation(correlation)
        async with self._lock:
            existing = await _to_thread_complete(self._recover, correlation)
            if existing is None:
                raise DockerBootstrapStoreError("Docker bootstrap allocation does not exist")
            if (
                existing.material.environment_id != material.environment_id
                or existing.material.configuration_fingerprint != material.configuration_fingerprint
                or existing.material.envd_configuration != material.envd_configuration
            ):
                raise DockerBootstrapStoreError("Docker bootstrap replacement changes immutable material")
            return await _to_thread_complete(self._replace_material, correlation, material)

    async def remove(self, correlation: str) -> None:
        _validate_correlation(correlation)
        async with self._lock:
            await _to_thread_complete(self._remove, correlation)

    def _publish_new(
        self,
        correlation: str,
        material: DockerBootstrapMaterial,
    ) -> DockerBootstrapAllocation:
        self._root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{correlation}-", dir=self._root))
        try:
            _write_material(temporary, material)
            _fsync_directory(temporary)
            target = self._root / correlation
            try:
                temporary.rename(target)
                _fsync_directory(self._root)
            except FileExistsError as error:
                existing = self._recover(correlation)
                if existing is None or existing.material.digest != material.digest:
                    raise DockerBootstrapStoreError(
                        "Docker bootstrap correlation was concurrently published with different material"
                    ) from error
                return existing
            published = self._recover(correlation)
            if published is None or published.material.digest != material.digest:
                raise DockerBootstrapStoreError("Docker bootstrap allocation could not be verified after publication")
            return published
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _replace_material(
        self,
        correlation: str,
        material: DockerBootstrapMaterial,
    ) -> DockerBootstrapAllocation:
        target = self._root / correlation
        temporary = Path(tempfile.mkdtemp(prefix=".replacement-", dir=target))
        try:
            _write_file(temporary / "credential", material.credential.encode(), 0o644)
            os.replace(temporary / "credential", target / "credential")
            _fsync_directory(target)
        except OSError as error:
            raise DockerBootstrapStoreError("Docker bootstrap credential could not be replaced") from error
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        recovered = self._recover(correlation)
        if recovered is None or recovered.material.digest != material.digest:
            raise DockerBootstrapStoreError("Docker bootstrap replacement could not be verified")
        return recovered

    def _recover(self, correlation: str) -> DockerBootstrapAllocation | None:
        target = self._root / correlation
        try:
            if not target.exists():
                return None
            if not target.is_dir():
                raise DockerBootstrapStoreError("Docker bootstrap allocation path is not a directory")
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            configuration = (target / "envd.json").read_bytes()
            credential = (target / "credential").read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise DockerBootstrapStoreError("Docker bootstrap allocation is incomplete") from error
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise DockerBootstrapStoreError("Docker bootstrap allocation cannot be read") from error
        try:
            material = DockerBootstrapMaterial(
                environment_id=manifest["environment_id"],
                configuration_fingerprint=manifest["configuration_fingerprint"],
                envd_configuration=configuration,
                credential=credential,
            )
        except (KeyError, TypeError) as error:
            raise DockerBootstrapStoreError("Docker bootstrap manifest is invalid") from error
        configuration_digest = f"sha256:{hashlib.sha256(configuration).hexdigest()}"
        if manifest.get("configuration_digest") != configuration_digest or not credential:
            raise DockerBootstrapStoreError("Docker bootstrap material does not match its manifest")
        return DockerBootstrapAllocation(correlation=correlation, directory=target.resolve(), material=material)

    def _remove(self, correlation: str) -> None:
        target = self._root / correlation
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            return
        except OSError as error:
            raise DockerBootstrapStoreError("Docker bootstrap allocation could not be removed") from error


async def _to_thread_complete[T](function: Callable[..., T], *arguments: Any) -> T:
    task = asyncio.create_task(asyncio.to_thread(function, *arguments))
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if task.cancelled():
                raise
            if cancellation is None:
                cancellation = error
            continue
        except BaseException as error:
            if cancellation is not None:
                cancellation.add_note(f"Docker blocking operation also failed: {error!r}")
                raise cancellation from None
            raise
        if cancellation is not None:
            raise cancellation from None
        return result


class DockerSDKEngine(DockerEngine):
    """Docker SDK implementation with every blocking call isolated from the event loop."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_env(cls, *, timeout_seconds: int = 30) -> DockerSDKEngine:
        if timeout_seconds <= 0:
            raise ValueError("Docker client timeout must be positive")
        import docker
        from docker.constants import DEFAULT_DOCKER_API_VERSION

        return cls(
            docker.from_env(
                timeout=timeout_seconds,
                version=DEFAULT_DOCKER_API_VERSION,
            )
        )

    async def validate_local_topology(self) -> None:
        await asyncio.to_thread(self._validate_local_topology)

    async def inspect_image(self, reference: str) -> DockerImageInspection | None:
        return await asyncio.to_thread(self._inspect_image, reference)

    async def pull_image(self, reference: str) -> None:
        await asyncio.to_thread(self._pull_image, reference)

    async def validate_mount(self, mount: DockerEngineMount) -> None:
        await asyncio.to_thread(self._validate_mount, mount)

    async def create_container(self, spec: DockerContainerSpec) -> str:
        return await _to_thread_complete(self._create_container, spec)

    async def inspect_container(self, container_id: str) -> DockerContainerInspection | None:
        return await asyncio.to_thread(self._inspect_container, container_id)

    async def find_containers(self, labels: Mapping[str, str]) -> tuple[DockerContainerInspection, ...]:
        return await asyncio.to_thread(self._find_containers, dict(labels))

    async def start_container(self, container_id: str) -> None:
        await _to_thread_complete(self._start_container, container_id)

    async def stop_container(self, container_id: str, *, timeout_seconds: int) -> None:
        await _to_thread_complete(self._stop_container, container_id, timeout_seconds)

    async def remove_container(self, container_id: str) -> None:
        await _to_thread_complete(self._remove_container, container_id)

    def _validate_local_topology(self) -> None:
        base_url = str(self._client.api.base_url)
        parsed = urlparse(base_url)
        local_socket_schemes = {"unix", "npipe"}
        local_network_schemes = {"tcp", "http", "https", "http+docker"}
        is_local_socket = parsed.scheme in local_socket_schemes
        is_local_network = parsed.scheme in local_network_schemes and parsed.hostname in {
            "127.0.0.1",
            "localhost",
            "localnpipe",
            "::1",
        }
        if not is_local_socket and not is_local_network:
            raise DockerEngineError("Docker Engine endpoint is not local", code="docker_topology_invalid")
        try:
            self._client.ping()
        except Exception as error:
            raise DockerEngineError("Local Docker Engine is unavailable", code="docker_engine_unavailable") from error

    def _inspect_image(self, reference: str) -> DockerImageInspection | None:
        try:
            image = self._client.images.get(reference)
        except Exception as error:
            if _is_not_found(error):
                return None
            raise DockerEngineError("Docker image inspection failed") from error
        config = image.attrs.get("Config") or {}
        return DockerImageInspection(image_id=image.id, user=str(config.get("User") or ""))

    def _pull_image(self, reference: str) -> None:
        try:
            self._client.images.pull(reference)
        except Exception as error:
            raise DockerEngineError("Docker image pull failed", code="docker_image_pull_failed") from error

    def _validate_mount(self, mount: DockerEngineMount) -> None:
        if mount.type == "bind":
            source = Path(mount.source)
            if not source.is_absolute() or not source.exists() or not source.is_dir():
                raise DockerEngineError(
                    "Docker bind source must be an existing absolute directory", code="docker_mount_invalid"
                )
            return
        try:
            self._client.volumes.get(mount.source)
        except Exception as error:
            code = "docker_mount_missing" if _is_not_found(error) else "docker_mount_invalid"
            raise DockerEngineError("Docker named volume could not be validated", code=code) from error

    def _create_container(self, spec: DockerContainerSpec) -> str:
        from docker.types import Mount

        mounts = [
            Mount(
                target=mount.target,
                source=mount.source,
                type=mount.type,
                read_only=mount.read_only,
            )
            for mount in spec.mounts
        ]
        options: dict[str, Any] = {
            "image": spec.image_id,
            "command": list(spec.command),
            "detach": True,
            "environment": dict(spec.environment),
            "labels": dict(spec.labels),
            "mounts": mounts,
            "ports": {f"{spec.eip_container_port}/tcp": ("127.0.0.1", None)},
        }
        if spec.nano_cpus is not None:
            options["nano_cpus"] = spec.nano_cpus
        if spec.memory_bytes is not None:
            options["mem_limit"] = spec.memory_bytes
        if spec.pids_limit is not None:
            options["pids_limit"] = spec.pids_limit
        try:
            container = self._client.containers.create(**options)
            return _normalized_id(container.id)
        except Exception as error:
            raise DockerEngineError("Docker container create failed", dispatched=True) from error

    def _inspect_container(self, container_id: str) -> DockerContainerInspection | None:
        try:
            container = self._client.containers.get(_docker_id(container_id))
            container.reload()
        except Exception as error:
            if _is_not_found(error):
                return None
            raise DockerEngineError("Docker container inspection failed") from error
        return _inspection(container.attrs)

    def _find_containers(self, labels: Mapping[str, str]) -> tuple[DockerContainerInspection, ...]:
        filters = {"label": [f"{key}={value}" for key, value in sorted(labels.items())]}
        try:
            containers = self._client.containers.list(all=True, filters=filters)
            inspections: list[DockerContainerInspection] = []
            for container in containers:
                container.reload()
                inspections.append(_inspection(container.attrs))
            return tuple(inspections)
        except Exception as error:
            raise DockerEngineError("Docker container query failed") from error

    def _start_container(self, container_id: str) -> None:
        try:
            self._client.containers.get(_docker_id(container_id)).start()
        except Exception as error:
            raise DockerEngineError("Docker container start failed", dispatched=True) from error

    def _stop_container(self, container_id: str, timeout_seconds: int) -> None:
        try:
            self._client.containers.get(_docker_id(container_id)).stop(timeout=timeout_seconds)
        except Exception as error:
            raise DockerEngineError("Docker container stop failed", dispatched=True) from error

    def _remove_container(self, container_id: str) -> None:
        try:
            self._client.containers.get(_docker_id(container_id)).remove()
        except Exception as error:
            if _is_not_found(error):
                return
            raise DockerEngineError("Docker container remove failed", dispatched=True) from error


def _write_material(directory: Path, material: DockerBootstrapMaterial) -> None:
    directory.chmod(0o755)
    _write_file(directory / "envd.json", material.envd_configuration, 0o644)
    _write_file(directory / "credential", material.credential.encode(), 0o644)
    manifest = json.dumps(
        {
            "configuration_digest": f"sha256:{hashlib.sha256(material.envd_configuration).hexdigest()}",
            "configuration_fingerprint": material.configuration_fingerprint,
            "environment_id": material.environment_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    _write_file(directory / "manifest.json", manifest, 0o644)


def _write_file(path: Path, content: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _inspection(attributes: Mapping[str, Any]) -> DockerContainerInspection:
    config = attributes.get("Config") or {}
    host_config = attributes.get("HostConfig") or {}
    state = attributes.get("State") or {}
    network = attributes.get("NetworkSettings") or {}
    ports = network.get("Ports") or {}
    published_ports = {key: value for key, value in ports.items() if value}
    published = published_ports.get(_EIP_PORT_KEY)
    route = published[0] if published else {}
    configured_ports = {key: value for key, value in (host_config.get("PortBindings") or {}).items() if value}
    configured = configured_ports.get(_EIP_PORT_KEY)
    binding = configured[0] if configured else {}
    binding_exact = (
        set(configured_ports) == {_EIP_PORT_KEY}
        and configured is not None
        and len(configured) == 1
        and binding.get("HostIp") == "127.0.0.1"
    )
    route_exact = (
        set(published_ports) == {_EIP_PORT_KEY}
        and published is not None
        and len(published) == 1
        and route.get("HostIp") == "127.0.0.1"
        and (
            not binding.get("HostPort") or not route.get("HostPort") or binding.get("HostPort") == route.get("HostPort")
        )
    )
    environment: dict[str, str] = {}
    for entry in config.get("Env") or ():
        name, separator, value = str(entry).partition("=")
        if separator:
            environment[name] = value
    mounts = tuple(
        DockerEngineMount(
            type="volume" if mount.get("Type") == "volume" else "bind",
            source=str(mount.get("Name") or mount.get("Source") or ""),
            target=str(mount.get("Destination") or ""),
            read_only=not bool(mount.get("RW", True)),
        )
        for mount in attributes.get("Mounts") or ()
    )
    nano_cpus = int(host_config.get("NanoCpus") or 0) or None
    memory = int(host_config.get("Memory") or 0) or None
    pids = int(host_config.get("PidsLimit") or 0) or None
    command = tuple(str(value) for value in config.get("Cmd") or ())
    return DockerContainerInspection(
        container_id=str(attributes.get("Id") or ""),
        image_id=str(attributes.get("Image") or ""),
        status=str(state.get("Status") or ""),
        user=str(config.get("User") or ""),
        command=command,
        environment=environment,
        labels=dict(config.get("Labels") or {}),
        mounts=mounts,
        eip_host_ip=str(route.get("HostIp") or binding.get("HostIp") or "") or None,
        eip_host_port=int(route["HostPort"]) if route.get("HostPort") else None,
        eip_binding_exact=binding_exact,
        eip_route_exact=route_exact,
        nano_cpus=nano_cpus,
        memory_bytes=memory,
        pids_limit=pids,
    )


def _is_not_found(error: Exception) -> bool:
    try:
        from docker.errors import NotFound
    except ImportError:
        return False
    return isinstance(error, NotFound)


def _normalized_id(value: str) -> str:
    matched = _CONTAINER_ID.fullmatch(value)
    if matched is None:
        raise ValueError("Docker immutable ID must be one 64-character lowercase SHA-256 digest")
    return f"sha256:{matched.group('digest')}"


def _docker_id(value: str) -> str:
    return _normalized_id(value).removeprefix("sha256:")


def _validate_correlation(value: str) -> None:
    if _BOOTSTRAP_CORRELATION.fullmatch(value) is None:
        raise ValueError("Docker bootstrap correlation is invalid")
