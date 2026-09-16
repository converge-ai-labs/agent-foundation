"""Preparation installs only checkout-owned dependencies, even without SDK repositories."""

from pathlib import Path
from types import SimpleNamespace

from dev.service import preparation


def _write(root: Path, path: str, content: str = "input") -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def _workspace(root: Path) -> None:
    for path in (
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "packages/a13n-service/pyproject.toml",
        "frontend/package.json",
        "frontend/pnpm-lock.yaml",
        "frontend/pnpm-workspace.yaml",
        "frontend/apps/a13n-console/package.json",
        "frontend/apps/a13n-console/src/service-client/client.ts",
    ):
        _write(root, path)


def _runner(root: Path, calls: list[tuple[str, ...]]):
    def run(command, **_kwargs):
        command = tuple(command)
        calls.append(command)
        if command[0] == "uv":
            _write(root, ".venv/bin/python")
            _write(root, ".venv/lib/python3.13/site-packages/_editable_impl_a13n_service.pth")
            _write(root, ".venv/lib/python3.13/site-packages/a13n_service-0.dist-info/METADATA")
        elif command[0] == "pnpm":
            _write(root, "frontend/node_modules/.pnpm/lock.yaml")
            _write(root, "frontend/apps/a13n-console/node_modules/vite/package.json")
            _write(root, "frontend/apps/a13n-console/node_modules/react/package.json")
        else:
            raise AssertionError(f"Unexpected preparation command: {command}")
        return SimpleNamespace(returncode=0)

    return run


def test_source_edit_does_not_reinstall_or_build_an_external_sdk(tmp_path, monkeypatch):
    _workspace(tmp_path)
    calls = []
    monkeypatch.setattr(preparation.subprocess, "run", _runner(tmp_path, calls))
    preparation.prepare(tmp_path, console=True)
    assert sorted(command[0] for command in calls) == ["pnpm", "uv"]
    assert not (tmp_path / "sdk").exists()

    calls.clear()
    _write(tmp_path, "frontend/apps/a13n-console/src/service-client/client.ts", "changed")
    preparation.prepare(tmp_path, console=True)
    assert calls == []


def test_missing_install_outputs_trigger_only_their_owner(tmp_path, monkeypatch):
    _workspace(tmp_path)
    calls = []
    monkeypatch.setattr(preparation.subprocess, "run", _runner(tmp_path, calls))
    preparation.prepare(tmp_path, console=True)

    calls.clear()
    (tmp_path / ".venv/lib/python3.13/site-packages/_editable_impl_a13n_service.pth").unlink()
    preparation.prepare(tmp_path, console=True)
    assert [command[0] for command in calls] == ["uv"]

    calls.clear()
    (tmp_path / "frontend/apps/a13n-console/node_modules/vite/package.json").unlink()
    preparation.prepare(tmp_path, console=True)
    assert [command[0] for command in calls] == ["pnpm"]


def test_frontend_dependency_change_invalidates_only_frontend_install(tmp_path, monkeypatch):
    _workspace(tmp_path)
    calls = []
    monkeypatch.setattr(preparation.subprocess, "run", _runner(tmp_path, calls))
    preparation.prepare(tmp_path, console=True)
    calls.clear()
    _write(tmp_path, "frontend/pnpm-lock.yaml", "new compiler version")
    preparation.prepare(tmp_path, console=True)
    assert calls == [("pnpm", "--dir", "frontend", "install", "--frozen-lockfile")]


def test_service_only_preparation_does_not_install_frontend(tmp_path, monkeypatch):
    _workspace(tmp_path)
    calls = []
    monkeypatch.setattr(preparation.subprocess, "run", _runner(tmp_path, calls))
    preparation.prepare(tmp_path, console=False)
    assert [command[0] for command in calls] == ["uv"]
