from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from a13n_envd_client.eip.v1 import EIP_DESCRIPTOR_SHA256

from scripts.eip_codegen.__main__ import (
    ARTIFACT_PATH,
    DESCRIPTOR_PATH,
    GENERATED_HEADER,
    MANIFEST_PATH,
    PYTHON_PATH,
    verify_generated,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
OPENRPC_PATH = REPOSITORY_ROOT / "proto/a13n-envd/eip/v1/artifacts/openrpc.json"
SCHEMA_PATH = REPOSITORY_ROOT / "proto/a13n-envd/eip/v1/artifacts/schema.json"
DATA_FRAME_PROFILE_PATH = REPOSITORY_ROOT / "proto/a13n-envd/eip/v1/artifacts/data-frame-profile.json"
METHODS_PATH = REPOSITORY_ROOT / "proto/a13n-envd/eip/v1/artifacts/methods.json"


def write_generated_tree(root: Path) -> None:
    descriptor = b"test EIP descriptor"
    files = {
        DESCRIPTOR_PATH: descriptor,
        PYTHON_PATH / "models.py": f"{GENERATED_HEADER}VALUE = 1\n".encode(),
        ARTIFACT_PATH / "methods.json": b'{"generated":true}\n',
    }
    manifest_files = sorted([*(path.as_posix() for path in files), MANIFEST_PATH.as_posix()])
    manifest = {
        "generated": True,
        "descriptor_sha256": hashlib.sha256(descriptor).hexdigest(),
        "files": manifest_files,
    }
    files[MANIFEST_PATH] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def snapshot(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_checked_inspection_artifacts_follow_eip_json_profile() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]
    assert schema["EIPPath"]["properties"]["path"]["format"] == "eip-absolute-path"
    assert schema["EncodedBytes"]["properties"]["data"]["format"] == "eip-base64-unpadded"
    generation = schema["SessionDescriptor"]["properties"]["generation"]
    assert generation["minimum"] == 1
    assert generation["maximum"] == 2**64 - 1
    assert "execution_features" in schema["SessionDescriptor"]["required"]
    assert set(schema["ExecutionFeatures"]["required"]) == {
        "process_count_limit",
        "memory_bytes_limit",
        "cpu_time_limit",
        "signal_interrupt",
        "signal_terminate",
    }
    assert "digest" in schema["FileReadCompletion"]["required"]
    assert schema["EIPError"]["properties"]["code"]["minimum"] == -(2**31)
    for model_name, selector_name, removed_selector in [
        ("OutputReadParams", "start_offset", "cursor"),
        ("OutputReleaseParams", "reference", "cursor"),
        ("ReceiptGetParams", "operation_id", "receipt_ref"),
    ]:
        assert selector_name in schema[model_name]["required"]
        assert removed_selector not in schema[model_name]["properties"]

    openrpc = json.loads(OPENRPC_PATH.read_text(encoding="utf-8"))
    shell_exec = next(method for method in openrpc["methods"] if method["name"] == "shell.exec")
    assert [item["name"] for item in shell_exec["params"]] == ["context", "request"]
    assert all(item["required"] is True for item in shell_exec["params"])
    receipt_get = next(method for method in openrpc["methods"] if method["name"] == "receipt.get")
    assert receipt_get["x-eip-params-schema"] == {"$ref": "schema.json#/$defs/ReceiptGetParams"}
    readiness = next(method for method in openrpc["methods"] if method["name"] == "environment.readiness")
    assert readiness["x-eip-replay-class"] == "active_only"
    assert readiness["x-eip-params-schema"] == {"$ref": "schema.json#/$defs/EnvironmentReadinessParams"}
    assert readiness["result"]["schema"] == {"$ref": "schema.json#/$defs/EnvironmentReadinessResult"}
    readiness_result = schema["EnvironmentReadinessResult"]
    assert set(readiness_result["required"]) == {"ready", "device_id", "generation", "session_id"}
    assert readiness_result["properties"]["generation"]["minimum"] == 1
    assert readiness_result["properties"]["device_id"]["minLength"] == 1

    methods = json.loads(METHODS_PATH.read_text(encoding="utf-8"))
    assert methods["method_count"] == 42
    assert sum(method["replay_class"] == "active_only" for method in methods["methods"]) == 18
    assert sum(method["replay_class"] == "terminal_evidence" for method in methods["methods"]) == 16
    assert sum(method["replay_class"] == "ledger_external" for method in methods["methods"]) == 8
    assert {method["jsonrpc_method"] for method in methods["methods"] if method["device_scoped"]} == {
        "initialize",
        "device.describe",
        "directory.list",
        "session.open",
    }
    assert "mount_id" not in schema["EIPPath"]["properties"]
    assert {"device_id", "path_style", "default_working_directory", "directory_discovery"}.issubset(
        schema["DeviceDescriptor"]["required"]
    )
    descriptor = (REPOSITORY_ROOT / DESCRIPTOR_PATH).read_bytes()
    manifest = json.loads((REPOSITORY_ROOT / MANIFEST_PATH).read_text(encoding="utf-8"))
    descriptor_sha256 = hashlib.sha256(descriptor).hexdigest()
    assert manifest["descriptor_sha256"] == descriptor_sha256
    assert EIP_DESCRIPTOR_SHA256 == descriptor_sha256
    transfers = [method for method in methods["methods"] if method["transfer_action"] is not None]
    assert {method["jsonrpc_method"] for method in transfers} == {
        "file.open_reader",
        "file.close_reader",
        "file.open_writer",
        "file.commit_writer",
        "file.abort_writer",
    }

    profile = json.loads(DATA_FRAME_PROFILE_PATH.read_text(encoding="utf-8"))
    assert profile["magic_ascii"] == "EIPD"
    assert profile["profile_version"] == 1
    assert profile["header_bytes"] == 24
    assert profile["transfer_window_chunks"] == 8
    assert profile["kinds"] == {
        "attach": 1,
        "attached": 2,
        "chunk": 3,
        "end": 4,
        "end_ack": 5,
        "reset": 6,
        "credit": 7,
    }
    assert sum(field["width"] for field in profile["fields"]) == 24


def test_verify_generated_does_not_modify_checked_tree(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    repository = tmp_path / "repository"
    write_generated_tree(candidate)
    write_generated_tree(repository)
    before = snapshot(repository)

    verify_generated(candidate, repository)

    assert snapshot(repository) == before


def test_verify_generated_reports_changed_generated_file(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    repository = tmp_path / "repository"
    write_generated_tree(candidate)
    write_generated_tree(repository)
    (repository / PYTHON_PATH / "models.py").write_text(f"{GENERATED_HEADER}VALUE = 2\n", encoding="utf-8")

    with pytest.raises(SystemExit, match=r"generated file differs:.*models\.py"):
        verify_generated(candidate, repository)


def test_verify_generated_reports_untracked_marked_file(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    repository = tmp_path / "repository"
    write_generated_tree(candidate)
    write_generated_tree(repository)
    extra = repository / PYTHON_PATH / "obsolete.py"
    extra.write_text(f"{GENERATED_HEADER}VALUE = 1\n", encoding="utf-8")

    with pytest.raises(SystemExit, match=r"untracked generated file:.*obsolete\.py"):
        verify_generated(candidate, repository)
