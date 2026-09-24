"""Port blocks are reserved once per checkout, never move, and never overlap another checkout's."""

import json
import shutil
import socket
from pathlib import Path

import pytest

from dev.service.instance import ensure_instance, instance_file, load_instance, require_free


def test_checkouts_keep_stable_disjoint_blocks(tmp_path: Path, machine: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    reserved = ensure_instance(first)
    other = ensure_instance(second)

    assert ensure_instance(first) == reserved == load_instance(first)
    assert set(reserved.ports.named().values()).isdisjoint(other.ports.named().values())
    registry = json.loads((machine / "checkouts.json").read_text())
    assert set(registry) == {str(first.resolve()), str(second.resolve())}


def test_a_lost_instance_file_is_restored_from_the_registry(checkout_root: Path) -> None:
    reserved = ensure_instance(checkout_root)
    instance_file(checkout_root).unlink()
    assert load_instance(checkout_root) is None
    assert ensure_instance(checkout_root) == reserved


def test_a_copied_instance_file_is_refused(tmp_path: Path, checkout_root: Path) -> None:
    ensure_instance(checkout_root)
    copy = tmp_path / "copy"
    instance_file(copy).parent.mkdir(parents=True)
    instance_file(copy).write_text(instance_file(checkout_root).read_text())
    with pytest.raises(ValueError, match="belongs to another checkout"):
        ensure_instance(copy)


def test_a_deleted_checkout_releases_its_block(tmp_path: Path, machine: Path) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    ensure_instance(gone)
    shutil.rmtree(gone)
    survivor = tmp_path / "survivor"
    survivor.mkdir()
    ensure_instance(survivor)
    assert set(json.loads((machine / "checkouts.json").read_text())) == {str(survivor.resolve())}


def test_an_occupied_assigned_port_fails_instead_of_moving(checkout_root: Path) -> None:
    reserved = ensure_instance(checkout_root)
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", reserved.ports.console))
        holder.listen()
        with pytest.raises(ValueError, match=f"console 127.0.0.1:{reserved.ports.console}.*keeps its ports stable"):
            require_free(reserved.ports.named())
        assert ensure_instance(checkout_root) == reserved
