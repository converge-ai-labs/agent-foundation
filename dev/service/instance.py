"""Stable machine-wide port assignments for local source checkouts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from .state import atomic_write
from .state import machine_directory as _machine_directory

PORT_NAMES = ("service", "console", "model", "postgres", "redis", "mem0")


@dataclass(frozen=True, slots=True)
class Ports:
    service: int
    console: int
    model: int
    postgres: int
    redis: int
    mem0: int

    def values(self) -> tuple[int, ...]:
        return tuple(asdict(self).values())


@dataclass(frozen=True, slots=True)
class Instance:
    id: str
    root: str
    ports: Ports
    version: int = 1


def instance_path(root: Path) -> Path:
    return root / "var/dev/instance.json"


@contextmanager
def _machine_lock():
    directory = _machine_directory()
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "instances.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError(f"Invalid local instance file: {path}") from None
    if not isinstance(value, dict):
        raise ValueError(f"Invalid local instance file: {path}")
    return value


def _parse_instance(value: object, path: Path) -> Instance:
    if not isinstance(value, dict):
        raise ValueError(f"Invalid local instance record: {path}")
    ports = value.get("ports")
    if (
        type(value.get("id")) is not str
        or type(value.get("root")) is not str
        or type(value.get("version")) is not int
        or not isinstance(ports, dict)
        or set(ports) != set(PORT_NAMES)
        or any(type(ports.get(name)) is not int for name in PORT_NAMES)
    ):
        raise ValueError(f"Invalid local instance record: {path}")
    instance = Instance(
        id=value["id"],
        root=value["root"],
        ports=Ports(**{name: ports[name] for name in PORT_NAMES}),
        version=value["version"],
    )
    values = instance.ports.values()
    if instance.version != 1 or len(set(values)) != len(values) or any(not 1024 <= port <= 65535 for port in values):
        raise ValueError(f"Invalid local instance record: {path}")
    return instance


def load_instance(root: Path) -> Instance | None:
    path = instance_path(root)
    if not path.exists():
        return None
    instance = _parse_instance(_read(path), path)
    resolved = str(root.resolve())
    if instance.root != resolved or instance.id != _identity(root):
        raise ValueError(f"Local instance ownership does not match this checkout: {path}")
    return instance


def _identity(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


def _ports_available(ports: tuple[int, ...]) -> bool:
    sockets: list[socket.socket] = []
    try:
        for port in ports:
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            sockets.append(listener)
        return True
    except OSError:
        return False
    finally:
        for listener in sockets:
            listener.close()


def ensure_instance(root: Path) -> Instance:
    root = root.resolve()
    with _machine_lock():
        registry_path = _machine_directory() / "instances.json"
        registry = _read(registry_path) if registry_path.exists() else {}
        records = {}
        for path, value in registry.items():
            if not Path(path).exists() and path != str(root):
                continue
            record = _parse_instance(value, registry_path)
            if record.root != path or record.id != _identity(Path(path)):
                raise ValueError(f"Invalid machine registry ownership for checkout: {path}")
            records[path] = record
        if set(records) != set(registry):
            _write_json(registry_path, {path: asdict(value) for path, value in records.items()})
        existing = load_instance(root)
        registered = records.get(str(root))
        if existing is not None:
            conflicts = [
                path
                for path, value in records.items()
                if path != str(root) and set(value.ports.values()) & set(existing.ports.values())
            ]
            if conflicts:
                raise ValueError(f"Local instance ports conflict with registered checkout: {conflicts[0]}")
            if registered is not None and registered != existing:
                raise ValueError("Local instance file does not match its machine registry record")
            if registered is None:
                records[str(root)] = existing
                _write_json(registry_path, {path: asdict(value) for path, value in records.items()})
            return existing
        if registered is not None:
            if registered.root != str(root) or registered.id != _identity(root):
                raise ValueError("Machine registry ownership does not match this checkout")
            path = instance_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(path, asdict(registered))
            return registered
        reserved = {port for value in records.values() for port in value.ports.values()}
        start = 20000 + (int(_identity(root), 16) % 4000) * 8
        for offset in range(4000):
            base = 20000 + ((start - 20000 + offset * 8) % 32000)
            ports = Ports(base, base + 1, base + 2, base + 3, base + 4, base + 5)
            if not reserved.intersection(ports.values()) and _ports_available(ports.values()):
                break
        else:
            raise RuntimeError("No free local development port block is available")
        instance = Instance(id=_identity(root), root=str(root), ports=ports)
        records[str(root)] = instance
        _write_json(registry_path, {path: asdict(value) for path, value in records.items()})
        path = instance_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, asdict(instance))
        return instance


def _write_json(path: Path, value: dict) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
