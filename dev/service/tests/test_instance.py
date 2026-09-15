"""Worktree instance allocation is stable, exclusive, and side-effect free on status reads."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from dev.service import instance as instances
from dev.service.environment import LOCAL_CONFIG
from dev.service.resolution import resolve_environment


def test_two_checkouts_receive_stable_distinct_ports(tmp_path, monkeypatch):
    machine = tmp_path / "machine"
    monkeypatch.setattr(instances, "_machine_directory", lambda: machine)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()

    first = instances.ensure_instance(first_root)
    second = instances.ensure_instance(second_root)

    assert first == instances.ensure_instance(first_root)
    assert set(first.ports.values()).isdisjoint(second.ports.values())
    assert json.loads(instances.instance_path(first_root).read_text())["root"] == str(first_root)


def test_orphan_instance_is_reconciled_before_another_checkout_allocates(tmp_path, monkeypatch):
    machine = tmp_path / "machine"
    monkeypatch.setattr(instances, "_machine_directory", lambda: machine)
    first_root = (tmp_path / "first").resolve()
    second_root = (tmp_path / "second").resolve()
    first_root.mkdir()
    second_root.mkdir()
    ports = instances.Ports(21000, 21001, 21002, 21003, 21004, 21005)
    orphan = instances.Instance(instances._identity(first_root), str(first_root), ports)
    instances.instance_path(first_root).parent.mkdir(parents=True)
    instances._write_json(instances.instance_path(first_root), asdict(orphan))

    assert instances.ensure_instance(first_root) == orphan
    second = instances.ensure_instance(second_root)
    assert set(second.ports.values()).isdisjoint(ports.values())
    registry = json.loads((machine / "instances.json").read_text())
    assert set(registry) == {str(first_root), str(second_root)}


def test_interrupted_instance_write_recovers_from_machine_reservation(tmp_path, monkeypatch):
    machine = tmp_path / "machine"
    monkeypatch.setattr(instances, "_machine_directory", lambda: machine)
    root = (tmp_path / "checkout").resolve()
    root.mkdir()
    actual_write = instances._write_json

    def interrupt(path, value):
        if path == instances.instance_path(root):
            raise OSError("interrupted")
        actual_write(path, value)

    monkeypatch.setattr(instances, "_write_json", interrupt)
    with pytest.raises(OSError, match="interrupted"):
        instances.ensure_instance(root)
    reserved = json.loads((machine / "instances.json").read_text())[str(root)]
    assert not instances.instance_path(root).exists()

    monkeypatch.setattr(instances, "_write_json", actual_write)
    recovered = instances.ensure_instance(root)
    assert asdict(recovered) == reserved
    assert instances.load_instance(root) == recovered


@pytest.mark.parametrize("invalid", ["21000", True])
def test_malformed_port_types_are_actionable(tmp_path, invalid):
    root = tmp_path.resolve()
    path = instances.instance_path(root)
    path.parent.mkdir(parents=True)
    value = {
        "id": instances._identity(root),
        "root": str(root),
        "ports": {name: 21000 + index for index, name in enumerate(instances.PORT_NAMES)},
        "version": 1,
    }
    value["ports"]["service"] = invalid
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Invalid local instance record"):
        instances.load_instance(root)


def test_status_resolution_does_not_create_instance(tmp_path):
    assert resolve_environment(LOCAL_CONFIG, create=False, root=tmp_path) is None
    assert not instances.instance_path(tmp_path).exists()


def test_resolution_derives_all_local_boundaries_from_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(instances, "_machine_directory", lambda: tmp_path / "machine")
    environment = resolve_environment(LOCAL_CONFIG, root=tmp_path)
    assert environment is not None and environment.instance is not None
    ports = environment.instance.ports
    settings = environment.settings

    assert settings.service.port == ports.service
    assert settings.iam.public_origin == f"http://127.0.0.1:{ports.console}"
    assert settings.iam.session_cookie_name == f"a13n_session_{environment.instance.id}"
    assert settings.connectivity.http_origins[-1] == f"http://127.0.0.1:{ports.model}"
    assert settings.objects.local_root == tmp_path / "var/dev/service/objects"
    assert settings.filesystem.root == tmp_path / "var/dev/service/files"


def test_two_compose_instances_start_and_stop_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(instances, "_machine_directory", lambda: tmp_path / "machine")
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
    environments = [resolve_environment(LOCAL_CONFIG, root=root) for root in roots]
    assert all(environment is not None for environment in environments)
    first, second = environments
    assert first is not None and second is not None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda environment: environment.compose("up", "-d", "--wait"), (first, second)))
        first.compose("exec", "-T", "redis", "redis-cli", "SET", "owner", "first")
        second.compose("exec", "-T", "redis", "redis-cli", "SET", "owner", "second")
        assert first.compose("exec", "-T", "redis", "redis-cli", "GET", "owner", capture=True).strip() == "first"
        assert second.compose("exec", "-T", "redis", "redis-cli", "GET", "owner", capture=True).strip() == "second"

        first.compose("stop")
        assert second.compose("exec", "-T", "redis", "redis-cli", "PING", capture=True).strip() == "PONG"
    finally:
        for environment in (first, second):
            environment.compose("down", "--volumes", "--remove-orphans")
