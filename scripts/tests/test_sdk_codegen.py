"""Contract adapters and non-mutating generation checks have one small owner."""

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sdk_codegen", ROOT / "sdk/codegen/generate.py")
assert SPEC and SPEC.loader
codegen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codegen)


def test_adapter_keeps_real_typed_unions_and_shared_contract_unchanged() -> None:
    schema = json.loads((ROOT / "sdk/openapi.json").read_text())
    before = json.dumps(schema)
    rust = codegen.prepare(schema, "rust")
    python = codegen.prepare(schema, "python")
    assert json.dumps(schema) == before
    for name in ["ActorRef", "EnvironmentSelection"]:
        assert rust["components"]["schemas"][name] == schema["components"]["schemas"][name]
    assert rust["components"]["schemas"]["RunStatus"]["x-rust-unknown-enum"]
    assert "x-rust-unknown-enum" not in rust["components"]["schemas"]["PrincipalType"]
    assert python["components"]["schemas"]["UpdateAgentRequest"]["properties"]["name"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert "default" not in python["components"]["schemas"]["ThreadRunSubmissionRequest"]["properties"]["environment"]


def test_drift_checks_content_and_stale_files_without_mutation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(codegen, "ROOT", tmp_path)
    target, output = tmp_path / "committed", tmp_path / "regenerated"
    target.mkdir()
    output.mkdir()
    (target / "old.py").write_text("old")
    (target / "current.py").write_text("out of date")
    (output / "current.py").write_text("current")
    before = codegen.files(target)
    assert not codegen.install(output, target, check=True)
    assert codegen.files(target) == before
    assert codegen.install(output, target, check=False)
    assert codegen.files(target) == {"current.py": b"current"}
    assert codegen.install(output, target, check=True)


def test_all_native_operations_have_bindings_in_each_language() -> None:
    document = json.loads((ROOT / "sdk/openapi.json").read_text())
    operations = {
        operation["operationId"]
        for path in document["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "patch", "put", "delete", "head", "options"}
    }
    python = {path.stem for path in (ROOT / "sdk/python/a13n/generated/api").rglob("*.py")}
    rust = "\n".join(path.read_text() for path in (ROOT / "sdk/rust/src/generated/apis").glob("*.rs"))
    go = (ROOT / "sdk/go/generated/client.gen.go").read_text()
    typescript = (ROOT / "sdk/typescript/src/schema.ts").read_text()
    for operation in operations:
        assert operation in python
        assert f"pub async fn {operation}(" in rust
        go_name = "".join(word[:1].upper() + word[1:] for word in operation.split("_"))
        assert re.search(rf"\b{go_name}(?:WithBody)?\(", go), operation
        assert operation in typescript


def test_shared_inputs_trigger_language_gates_and_nonmutating_check() -> None:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-sdks.yml").read_text())
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])
    for language in ["go", "python", "rust", "typescript"]:
        assert "sdk/openapi.json" in filters[language]
        assert "sdk/codegen/**" in filters[language]
        assert "packages/**" in filters[language]
    runs = [step.get("run", "") for step in workflow["jobs"]["generated"]["steps"]]
    assert runs.count("make sdk-generated-check") == 1
    assert "make sdk-generate" not in runs


def test_default_response_and_property_named_default_are_not_annotations() -> None:
    document = {
        "components": {
            "schemas": {
                "Object": {
                    "type": "object",
                    "properties": {
                        "default": {"type": "string", "default": "server-value"},
                    },
                }
            }
        },
        "paths": {
            "/resource": {
                "get": {
                    "responses": {
                        "default": {
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    }
                }
            }
        },
    }
    projected = codegen.prepare(document, "python")
    assert projected["components"]["schemas"]["Object"]["properties"]["default"] == {"type": "string"}
    assert projected["paths"] == document["paths"]
