"""Bulk local-environment cleanup keeps ownership and shared resources separate."""

import pytest

from dev.service import envs, instance
from dev.service.lifecycle import lifecycle_lock


def registered(tmp_path, monkeypatch):
    machine = tmp_path / "machine"
    monkeypatch.setattr(instance, "_machine_directory", lambda: machine)
    monkeypatch.setattr(envs, "machine_directory", lambda: machine)
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
    values = [instance.ensure_instance(root) for root in roots]
    for root in roots:
        (root / "var/dev/service").mkdir()
        (root / "var/dev/service/data").write_text("local")
    return roots, values


def test_list_combines_worktrees_registration_and_docker_volumes(tmp_path, monkeypatch):
    roots, values = registered(tmp_path, monkeypatch)
    monkeypatch.setattr(envs, "_git_worktrees", lambda: {str(roots[0]): "main", str(tmp_path / "unused"): None})
    first_project = envs.PROJECT_PREFIX + values[0].id
    orphan_project = envs.PROJECT_PREFIX + "123456789abc"
    monkeypatch.setattr(
        envs, "_docker_projects", lambda: ({first_project: "running(2)"}, {first_project: 2, orphan_project: 1})
    )

    rows = envs.list_environments()

    assert next(row for row in rows if row["instance"] == values[0].id)["volumes"] == 2
    assert next(row for row in rows if row["instance"] == values[1].id)["worktree"] is False
    assert next(row for row in rows if row["root"] == str(tmp_path / "unused"))["instance"] is None
    assert next(row for row in rows if row["instance"] == "123456789abc")["unregistered"] is True
    assert not any("langfuse" in str(row) for row in rows)


def test_remove_only_selected_instance_and_preserves_other(tmp_path, monkeypatch):
    roots, values = registered(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(envs, "_compose_down", lambda selected, *, mem0: calls.append((selected.id, mem0)))
    monkeypatch.setattr(envs, "stop_background_applications", lambda root: calls.append((str(root), "stop")))

    envs._remove(values[0])

    assert calls == [(str(roots[0]), "stop"), (values[0].id, True), (values[0].id, False)]
    assert not (roots[0] / "var/dev").exists()
    assert (roots[1] / "var/dev/service/data").read_text() == "local"
    assert envs._registry() == {str(roots[1]): values[1]}


def test_remove_rejects_changed_instance_before_docker(tmp_path, monkeypatch):
    roots, values = registered(tmp_path, monkeypatch)
    monkeypatch.setattr(envs, "_compose_down", lambda *args, **kwargs: pytest.fail("Docker must not be touched"))
    instance.instance_path(roots[0]).unlink()

    with pytest.raises(ValueError, match="Instance file does not match"):
        envs._remove(values[0])
    assert (roots[0] / "var/dev/service/data").exists()


def test_remove_refuses_active_checkout(tmp_path, monkeypatch):
    roots, values = registered(tmp_path, monkeypatch)
    monkeypatch.setattr(envs, "_compose_down", lambda *args, **kwargs: pytest.fail("Docker must not be touched"))

    with lifecycle_lock(roots[0]):
        with pytest.raises(ValueError, match="already starting, running, resetting, or stopping"):
            envs._remove(values[0])
    assert (roots[0] / "var/dev/service/data").exists()


def test_compose_cleanup_targets_only_checkout_projects(tmp_path, monkeypatch):
    _roots, values = registered(tmp_path, monkeypatch)
    commands = []
    monkeypatch.setattr(envs.subprocess, "run", lambda command, **kwargs: commands.append(command))

    envs._compose_down(values[0], mem0=True)
    envs._compose_down(values[0], mem0=False)

    assert [command[command.index("--project-name") + 1] for command in commands] == [
        f"a13n-dev-v2-{values[0].id}-mem0",
        f"a13n-dev-v2-{values[0].id}",
    ]
    assert all(command[-3:] == ["down", "--volumes", "--remove-orphans"] for command in commands)
