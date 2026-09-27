from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts import verify_inputs


def test_python_metadata_and_static_tools_do_not_select_runtime_tests() -> None:
    old = {"project": {"description": "before", "dependencies": ["pydantic"]}, "tool": {"ruff": {"line-length": 100}}}
    new = deepcopy(old)
    new["project"]["description"] = "after"
    new["tool"]["ruff"]["line-length"] = 120
    assert verify_inputs.python_manifest(old, new) == (False, {"ruff"})
    new["project"]["dependencies"].append("anyio")
    assert verify_inputs.python_manifest(old, new) == (True, {"ruff"})


def test_pytest_configuration_keeps_runtime_validation() -> None:
    assert verify_inputs.python_manifest({}, {"tool": {"pytest": {"ini_options": {"timeout": 1}}}}) == (True, set())


@pytest.fixture
def lock() -> dict:
    return {
        "version": 1,
        "package": [
            {
                "name": "workspace",
                "source": {"virtual": "."},
                "dev-dependencies": {"dev": [{"name": "pytest"}, {"name": "core"}]},
            },
            {"name": "core", "source": {"editable": "packages/core"}, "dependencies": [{"name": "http-client"}]},
            {"name": "app", "source": {"editable": "packages/app"}, "dependencies": [{"name": "core"}]},
            {"name": "other", "source": {"editable": "packages/other"}},
            {"name": "http-client", "version": "1", "dependencies": [{"name": "transport"}]},
            {"name": "transport", "version": "1"},
            {"name": "pytest", "version": "1"},
        ],
    }


def test_python_lock_selects_transitive_consumers_and_preserves_shared_environment(lock: dict) -> None:
    new = deepcopy(lock)
    new["package"][-2]["version"] = "2"
    assert verify_inputs.python_lock(lock, new) == {"packages/core/tests", "packages/app/tests"}
    new["package"][-1]["version"] = "2"
    assert verify_inputs.python_lock(lock, new) is None
    assert verify_inputs.python_lock({}, new) is None


def test_python_lock_keeps_removed_dependency_consumers(lock: dict) -> None:
    new = deepcopy(lock)
    new["package"][1].pop("dependencies")
    new["package"] = [entry for entry in new["package"] if entry["name"] not in {"http-client", "transport"}]
    assert verify_inputs.python_lock(lock, new) == {"packages/core/tests", "packages/app/tests"}


def test_frontend_lock_follows_changed_transitive_snapshots_and_peers() -> None:
    old = {
        "lockfileVersion": "9.0",
        "importers": {
            "apps/a13n-console": {"dependencies": {"client": {"version": "1(peer@2)"}}},
            "apps/a13n-harness-ui": {"dependencies": {"other": {"version": "1"}}},
        },
        "packages": {"client@1": {}, "transport@1": {"integrity": "old"}},
        "snapshots": {"client@1(peer@2)": {"dependencies": {"transport": "1"}}, "other@1": {}, "transport@1": {}},
    }
    new = deepcopy(old)
    new["packages"]["transport@1"]["integrity"] = "new"
    assert verify_inputs.frontend_lock(old, new) == {"apps/a13n-console"}
    new["settings"] = {"autoInstallPeers": False}
    assert verify_inputs.frontend_lock(old, new) is None


def test_frontend_shared_dependency_changes_include_application_consumers() -> None:
    old = {"importers": {"packages/a13n-ui": {}}, "snapshots": {}}
    new = {"importers": {"packages/a13n-ui": {"dependencies": {"react": {"version": "19"}}}}, "snapshots": {}}
    assert verify_inputs.frontend_lock(old, new) == set(verify_inputs.FRONTEND_PROJECTS)


def test_documents_use_requested_base_and_handle_invalid_current_input(tmp_path: Path) -> None:
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()

    git("init", "-q")
    path = tmp_path / "package.json"
    path.write_text('{"description":"first"}')
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "first")
    first = git("rev-parse", "HEAD")
    path.write_text('{"description":"second"}')
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "second")
    assert verify_inputs.documents(tmp_path, "package.json", first) == (
        {"description": "first"},
        {"description": "second"},
    )
    path.write_text("invalid")
    assert verify_inputs.documents(tmp_path, "package.json", first) is None
