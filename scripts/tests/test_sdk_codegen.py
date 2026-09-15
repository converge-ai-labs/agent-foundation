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


def test_ci_routes_contract_inputs_and_sdk_changes_without_fanout() -> None:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-sdks.yml").read_text())
    jobs = workflow["jobs"]
    filters = yaml.safe_load(jobs["changes"]["steps"][1]["with"]["filters"])
    cases = {
        "packages/a13n-service/a13n_service/search/router.py": {"generated"},
        "packages/a13n-harness/a13n_harness/types.py": {"generated"},
        "packages/a13n-harness-ui/tests/test_cli_interactions.py": set(),
        "packages/a13n-environment/tests/test_direct_local.py": set(),
        "packages/a13n-envd-client/tests/eip/test_stdio_e2e.py": set(),
        "packages/a13n-logging/tests/test_logging.py": set(),
        "packages/a13n-service/tests/process/test_openapi.py": {"generated"},
        "packages/a13n-service/tests/models/conftest.py": {"generated"},
        "packages/a13n-service/tests/models/test_router.py": {"generated"},
        "packages/a13n-harness-ui/pyproject.toml": {"generated"},
        "packages/a13n-service/README.md": {"generated"},
        "conftest.py": {"generated"},
        "uv.lock": {"generated"},
        "sdk/openapi.json": {"generated"},
        "sdk/codegen/rust/model.mustache": {"generated"},
        "scripts/tests/test_sdk_codegen.py": {"generated"},
        "sdk/python/a13n/client.py": {"python"},
        "sdk/python/a13n/generated/models/agent.py": {"generated", "python"},
        "sdk/go/generated/client.gen.go": {"generated", "go"},
        "sdk/rust/src/generated/apis/mod.rs": {"generated", "rust", "a13n_service_cli"},
        "sdk/rust/tests/generated.rs": {"rust"},
        "sdk/rust/a13n-service-cli/src/main.rs": {"a13n_service_cli"},
        "sdk/typescript/src/schema.ts": {"generated", "typescript"},
        "sdk/typescript/src/client.ts": {"typescript"},
        "sdk/fixtures/wire.json": {"python", "go", "rust", "typescript"},
        "Makefile": set(filters),
        ".github/workflows/ci-sdks.yml": set(filters),
        "Cargo.toml": set(),
        "docs/a13n-service/sdks.md": set(),
    }
    # These filters deliberately use only positive path globs. In paths-filter,
    # a separate negated pattern is an alternative, not a directory exclusion.
    assert all(not pattern.startswith("!") for patterns in filters.values() for pattern in patterns)
    events = workflow[True]  # PyYAML's YAML 1.1 loader reads `on` as True.
    for path, expected in cases.items():
        actual = {name for name, patterns in filters.items() if any(Path(path).full_match(p) for p in patterns)}
        assert actual == expected, path
        for event in ["pull_request", "push"]:
            assert any(Path(path).full_match(p) for p in events[event]["paths"]) == bool(expected), (event, path)
    for output in filters:
        job = jobs[output.replace("_", "-")]
        assert job["needs"] == "changes"
        assert job["if"] == f"github.event_name == 'workflow_dispatch' || needs.changes.outputs.{output} == 'true'"
        assert jobs["changes"]["outputs"][output] == "${{ steps.filter.outputs." + output + " }}"
    runs = [step.get("run", "") for step in jobs["generated"]["steps"]]
    assert runs.count("make sdk-generated-check") == 1
    assert "make sdk-generate" not in runs
    assert not any("setup-uv" in step.get("uses", "") for step in jobs["typescript"]["steps"])
    makefile = (ROOT / "Makefile").read_text()
    assert "sdk-check-all: sdk-generated-check " in makefile
    for target in ["sdk-typescript-check", "sdk-typescript-check-all"]:
        prerequisites = re.search(rf"^{target}: (.+)$", makefile, re.MULTILINE)
        assert prerequisites and "sdk-typescript-contract-check" not in prerequisites[1]


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
