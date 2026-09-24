"""Stable per-checkout identity and loopback ports, reserved machine-wide.

Stdlib only: `make dev-status` and `make dev-env-list` run it before the repository environment exists.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from dev.observability.state import atomic_write, machine_directory

# Each checkout takes one aligned block below the Linux ephemeral range (32768+).
FIRST_PORT = 20000
BLOCK = 8
BLOCKS = (32768 - FIRST_PORT) // BLOCK


@dataclass(frozen=True, slots=True)
class Ports:
    service: int
    console: int
    model: int
    postgres: int
    redis: int

    def named(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Instance:
    id: str
    root: str
    ports: Ports


def identity(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


def instance_file(root: Path) -> Path:
    return root / "var/dev/instance.json"


def _registry_file() -> Path:
    return machine_directory() / "checkouts.json"


def _parse(value: object, source: Path) -> Instance:
    names = [field.name for field in fields(Ports)]
    ports = value.get("ports") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("id"), str)
        or not isinstance(value.get("root"), str)
        or not isinstance(ports, dict)
        or sorted(ports) != sorted(names)
        or any(type(ports[name]) is not int or not 1024 <= ports[name] <= 65535 for name in names)
        or len(set(ports.values())) != len(names)
    ):
        raise ValueError(f"Invalid local instance record in {source}")
    return Instance(value["id"], value["root"], Ports(**ports))


def _read(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise ValueError(f"Unreadable local instance file: {path}") from None


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def load_instance(root: Path) -> Instance | None:
    """This checkout's recorded instance, without creating or reserving anything."""
    root = root.resolve()
    path = instance_file(root)
    if not path.exists():
        return None
    instance = _parse(_read(path), path)
    if instance.root != str(root) or instance.id != identity(root):
        raise ValueError(f"{path} belongs to another checkout; it was copied or the checkout moved")
    return instance


def registered_instances() -> dict[str, Instance]:
    path = _registry_file()
    if not path.exists():
        return {}
    records = _read(path)
    if not isinstance(records, dict):
        raise ValueError(f"Invalid machine registry: {path}")
    return {root: _parse(value, path) for root, value in records.items()}


def save_registry(records: dict[str, Instance]) -> None:
    _write(_registry_file(), {root: asdict(instance) for root, instance in sorted(records.items())})


@contextmanager
def machine_lock() -> Iterator[None]:
    """Serialize port reservations across every checkout on this machine."""
    directory = machine_directory()
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "checkouts.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def occupied(ports: dict[str, int]) -> list[str]:
    """The names of ports some other process holds on 127.0.0.1."""
    taken = []
    for name, port in ports.items():
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                taken.append(name)
    return taken


def require_free(ports: dict[str, int]) -> None:
    taken = occupied(ports)
    if taken:
        described = ", ".join(f"{name} 127.0.0.1:{ports[name]}" for name in taken)
        raise ValueError(
            f"Assigned port in use ({described}). This checkout keeps its ports stable (var/dev/instance.json); "
            "stop the process holding it and retry."
        )


def _allocate(root: Path, reserved: set[int]) -> Ports:
    start = int(identity(root), 16) % BLOCKS
    for offset in range(BLOCKS):
        base = FIRST_PORT + (start + offset) % BLOCKS * BLOCK
        ports = Ports(*range(base, base + len(fields(Ports))))
        if reserved.isdisjoint(ports.named().values()) and not occupied(ports.named()):
            return ports
    raise RuntimeError("No free local development port block is available")


def ensure_instance(root: Path) -> Instance:
    """This checkout's instance; the first call reserves a free port block that never moves afterwards."""
    root = root.resolve()
    with machine_lock():
        # Checkouts deleted from disk release their reservation.
        records = {path: record for path, record in registered_instances().items() if Path(path).is_dir()}
        recorded = load_instance(root)
        registered = records.get(str(root))
        if recorded and registered and recorded != registered:
            raise ValueError(f"{instance_file(root)} does not match this checkout's machine registration")
        others = {
            port for path, record in records.items() if path != str(root) for port in record.ports.named().values()
        }
        instance = recorded or registered
        if instance is None:
            instance = Instance(identity(root), str(root), _allocate(root, others))
        elif not others.isdisjoint(instance.ports.named().values()):
            raise ValueError(f"Ports in {instance_file(root)} are reserved by another registered checkout")
        records[str(root)] = instance
        save_registry(records)
        if recorded is None:
            _write(instance_file(root), asdict(instance))
        return instance
