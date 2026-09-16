"""Preparation invalidates installation and build work independently."""

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
        "sdk/typescript/package.json",
        "sdk/typescript/package-lock.json",
        "sdk/typescript/tsconfig.json",
        "sdk/typescript/tsconfig.build.json",
        "sdk/typescript/src/client.ts",
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
        elif command[-1] == "ci":
            _write(root, "sdk/typescript/node_modules/.package-lock.json")
            _write(root, "sdk/typescript/node_modules/.bin/tsc")
            _write(root, "sdk/typescript/node_modules/typescript/package.json")
        else:
            for suffix in (".js", ".js.map", ".d.ts", ".d.ts.map"):
                _write(root, f"sdk/typescript/dist/client{suffix}")
        return SimpleNamespace(returncode=0)

    return run


def test_source_edit_rebuilds_without_reinstalling_dependencies(tmp_path, monkeypatch):
    _workspace(tmp_path)
    calls = []
    monkeypatch.setattr(preparation.subprocess, "run", _runner(tmp_path, calls))
    preparation.prepare(tmp_path, console=True)
    assert sorted(command[0] for command in calls[:-1]) == ["npm", "pnpm", "uv"]
    assert calls[-1] == ("npm", "--prefix", "sdk/typescript", "run", "build")

    calls.clear()
    _write(tmp_path, "sdk/typescript/src/client.ts", "changed")
    preparation.prepare(tmp_path, console=True)
    assert calls == [("npm", "--prefix", "sdk/typescript", "run", "build")]


def test_missing_install_and_generated_outputs_trigger_only_their_owner(tmp_path, monkeypatch):
    _workspace(tmp_path)
    calls = []
    monkeypatch.setattr(preparation.subprocess, "run", _runner(tmp_path, calls))
    preparation.prepare(tmp_path, console=True)

    calls.clear()
    (tmp_path / ".venv/lib/python3.13/site-packages/_editable_impl_a13n_service.pth").unlink()
    (tmp_path / "frontend/apps/a13n-console/node_modules/vite/package.json").unlink()
    (tmp_path / "sdk/typescript/node_modules/.bin/tsc").unlink()
    (tmp_path / "sdk/typescript/dist/client.js").unlink()
    preparation.prepare(tmp_path, console=True)
    assert sorted(command[0] for command in calls[:-1]) == ["npm", "pnpm", "uv"]
    assert calls[-1] == ("npm", "--prefix", "sdk/typescript", "run", "build")


def test_sdk_dependency_change_also_invalidates_build(tmp_path, monkeypatch):
    _workspace(tmp_path)
    calls = []
    monkeypatch.setattr(preparation.subprocess, "run", _runner(tmp_path, calls))
    preparation.prepare(tmp_path, console=True)
    calls.clear()
    _write(tmp_path, "sdk/typescript/package-lock.json", "new compiler version")
    preparation.prepare(tmp_path, console=True)
    assert calls == [
        ("npm", "--prefix", "sdk/typescript", "ci"),
        ("npm", "--prefix", "sdk/typescript", "run", "build"),
    ]
