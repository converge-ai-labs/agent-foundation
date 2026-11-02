"""Service exports are checkout-owned, reproducible inputs, not SDK build outputs."""

import importlib.util
import json
from importlib.metadata import version
from pathlib import Path

import pytest
from a13n_service.resources.agents.schemas import AgentConfig

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("service_contract", ROOT / "scripts/export-a13n-service-openapi.py")
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def test_export_matches_service_http_and_existing_wire_models() -> None:
    exported = contract.documents()
    assert set(exported) == {
        contract.TARGET / "openapi.json",
        contract.TARGET / "session-stream.schema.json",
        contract.TARGET / "thread-stream.schema.json",
        contract.PLATFORM_TOOLS,
    }
    assert set(exported[contract.TARGET / "session-stream.schema.json"]["frames"]) == {
        "thread",
        "items",
        "delta",
        "app_ready",
        "gap",
        "schedule",
        "occurrence",
    }
    assert set(exported[contract.TARGET / "thread-stream.schema.json"]["frames"]) == {
        "delta",
        "boundary",
        "changed",
        "reset",
        "gap",
    }
    openapi = exported[contract.TARGET / "openapi.json"]
    paths = openapi["paths"]
    assert {"/healthz", "/readyz", "/api/v1/auth/login", "/api/v1/users/me"} <= paths.keys()
    assert all(path in {"/healthz", "/readyz"} or path.startswith("/api/v1/") for path in paths)
    assert openapi["info"]["version"] == version("a13n-service")
    for target, schema in exported.items():
        assert json.loads(target.read_text()) == schema


def test_exported_platform_tools_are_independent_of_http_admission() -> None:
    from a13n_service.platform_catalog import catalog

    tools = contract.documents()[contract.PLATFORM_TOOLS]
    assert {tool["key"] for tool in tools} == {tool.name for tool in catalog()}
    assert len({tool["key"] for tool in tools}) == len(tools)
    assert {tool["group"] for tool in tools} <= set(contract.PLATFORM_GROUPS)
    assert all(set(tool) == {"key", "group", "effect", "summary"} for tool in tools)
    assert all(tool["key"] and tool["summary"] for tool in tools)


def test_export_and_drift_check_work_without_sdk_directory(tmp_path: Path, monkeypatch, capsys) -> None:
    target = tmp_path / "proto/a13n-service"
    monkeypatch.setattr(contract, "TARGET", target)
    monkeypatch.setattr(contract, "SKILL_SCHEMA", tmp_path / "package/agent-config.json")
    monkeypatch.setattr(contract, "PLATFORM_SCHEMA", tmp_path / "package/platform-tools.json")
    monkeypatch.setattr(
        contract, "documents", lambda distribution: {target / "openapi.json": {"info": {"version": "1"}}}
    )
    monkeypatch.setattr("sys.argv", ["export"])
    contract.main()
    assert not (tmp_path / "sdk").exists()
    original = (target / "openapi.json").read_bytes()
    # Clean checkouts need no ignored release asset for committed drift checks.
    contract.PLATFORM_SCHEMA.unlink()
    monkeypatch.setattr("sys.argv", ["export", "--check"])
    contract.main()
    assert (target / "openapi.json").read_bytes() == original

    (target / "openapi.json").write_text("invalid")
    with pytest.raises(SystemExit, match="1"):
        contract.main()
    assert (target / "openapi.json").read_text() == "invalid"
    assert "make service-contract-generate" in capsys.readouterr().err

    (target / "openapi.json").unlink()
    with pytest.raises(SystemExit, match="1"):
        contract.main()
    assert not (target / "openapi.json").exists()


def test_console_dependency_and_development_commands_do_not_require_sdk() -> None:
    package = json.loads((ROOT / "frontend/apps/a13n-console/package.json").read_text())
    assert "@converge.ai/a13n" not in package["dependencies"]
    assert not any("sdk/" in value for value in package["dependencies"].values())
    for source in (ROOT / "frontend/apps/a13n-console/src").rglob("*"):
        if source.suffix in {".ts", ".tsx", ".mjs"}:
            assert "@converge.ai/a13n" not in source.read_text(), source
    makefile = (ROOT / "Makefile").read_text()
    for name in ("frontend-check", "frontend-test", "a13n-console-build"):
        definition = next(line for line in makefile.splitlines() if line.startswith(f"{name}:"))
        assert "sdk" not in definition


def test_bundled_skill_schema_is_current_and_can_be_built_independently(tmp_path, monkeypatch):
    assert json.loads(contract.SKILL_SCHEMA.read_text()) == AgentConfig.model_json_schema()
    target = tmp_path / "skills/schemas/agent-config.json"
    monkeypatch.setattr(contract, "SKILL_SCHEMA", target)
    platform_target = target.with_name("platform-tools.json")
    monkeypatch.setattr(contract, "PLATFORM_SCHEMA", platform_target)
    monkeypatch.setattr(contract, "documents", lambda: pytest.fail("Skill packaging must not export HTTP contracts"))
    monkeypatch.setattr("sys.argv", ["export", "--skills-only"])
    contract.main()
    assert json.loads(target.read_text()) == AgentConfig.model_json_schema()
    from a13n_service.platform_catalog import catalog

    generated = json.loads(platform_target.read_text())["tools"]
    invoked = {tool.name: tool for tool in catalog()}
    assert {tool["name"] for tool in generated} == invoked.keys()
    for tool in generated:
        definition = invoked[tool["name"]]
        assert tool["input_schema"] == definition.inputs.model_json_schema()
        assert (tool["group"], tool["effect"], tool["description"]) == (
            definition.group,
            definition.effect,
            definition.description,
        )
    monkeypatch.setattr("sys.argv", ["export", "--skills-only", "--check"])
    contract.main()
    target.write_text("{}")
    with pytest.raises(SystemExit, match="1"):
        contract.main()
    assert target.read_text() == "{}"
