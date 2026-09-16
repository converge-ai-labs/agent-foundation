"""Service exports are checkout-owned, reproducible inputs, not SDK build outputs."""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("service_contract", ROOT / "scripts/export-a13n-service-openapi.py")
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def test_export_matches_service_http_and_existing_wire_models() -> None:
    exported = contract.documents()
    assert all(path.startswith("/api/v1/") for path in exported["openapi.json"]["paths"])
    assert exported["openapi.json"]["info"]["version"] == "1"
    assert exported["notification-client.schema.json"] == TypeAdapter(contract.ClientFrame).json_schema()
    assert exported["run-stream-event.schema.json"] == contract.RunStreamEvent.model_json_schema()
    for name, schema in exported.items():
        assert json.loads((ROOT / "proto/a13n-service" / name).read_text()) == schema


def test_export_and_drift_check_work_without_sdk_directory(tmp_path: Path, monkeypatch, capsys) -> None:
    target = tmp_path / "proto/a13n-service"
    monkeypatch.setattr(contract, "TARGET", target)
    monkeypatch.setattr(contract, "documents", lambda: {"openapi.json": {"info": {"version": "1"}}})
    monkeypatch.setattr("sys.argv", ["export"])
    contract.main()
    assert not (tmp_path / "sdk").exists()
    original = (target / "openapi.json").read_bytes()
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
    for name in ("frontend-check", "frontend-test", "a13n-console-build", "live-test-model-console"):
        definition = next(line for line in makefile.splitlines() if line.startswith(f"{name}:"))
        assert "sdk" not in definition


def test_queue_delete_uses_the_accepted_query_and_no_content_contract() -> None:
    document = contract.documents()["openapi.json"]
    operation = document["paths"]["/api/v1/queued-submissions/{queued_submission_id}"]["delete"]
    assert "requestBody" not in operation
    version = next(parameter for parameter in operation["parameters"] if parameter["name"] == "expected_version")
    assert version["in"] == "query" and version["required"] is True
    assert version["schema"]["type"] == "integer" and version["schema"]["minimum"] == 1
    assert "204" in operation["responses"] and "200" not in operation["responses"]
    assert "content" not in operation["responses"]["204"]
    assert "DeleteQueuedSubmissionRequest" not in document["components"]["schemas"]
