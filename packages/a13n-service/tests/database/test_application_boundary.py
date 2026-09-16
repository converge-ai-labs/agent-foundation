"""Application dependencies are explicit distribution contributions."""

import ast
from importlib.util import resolve_name
from pathlib import Path

SOURCE = Path(__file__).parents[2] / "a13n_service"
COMPOSITION = {"app", "database.metadata", "process.lifecycle", "process.connectivity", "process.runtime"}


def test_only_application_and_composition_import_bots():
    errors = []
    for path in SOURCE.rglob("*.py"):
        relative = path.relative_to(SOURCE).with_suffix("")
        module = ".".join(relative.parts)
        if module.startswith("bots.") or module in COMPOSITION:
            continue
        package = "a13n_service." + ".".join(relative.parts[:-1])
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level:
                    name = resolve_name("." * node.level + name, package)
                names = [name, *(name + "." + item.name for item in node.names)]
            if any(name == "a13n_service.bots" or name.startswith("a13n_service.bots.") for name in names):
                errors.append(f"{module}:{node.lineno}")
    assert not errors, errors


def test_cold_core_schema_matches_declared_metadata(postgres_database):
    result = _core_schema(postgres_database)
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_composition_rejects_full_oss_database(service_database):
    result = _core_schema(service_database)
    assert result.returncode != 0
    assert "Can't locate revision" in result.stderr


def _core_schema(database):
    import os
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, str(Path(__file__).with_name("core_composition.py")), "--schema-only"],
        env={**os.environ, "A13N_CORE_TEST_DATABASE_URL": database.url.get_secret_value()},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
