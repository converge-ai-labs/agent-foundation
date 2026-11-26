from pathlib import Path

import pytest
import yaml

from scripts.classify_harness_pins import COMPATIBILITY_DOC, classify_changes, git, patch_bump

ROOT = Path(__file__).resolve().parents[2]
UI = "a13n-harness-ui"
SERVICE = "a13n-service"
MANIFEST = """\
[project]
version = "0.0.0"
dependencies = ["a13n-harness", "a13n-stream-protocol"]

[tool.a13n.release-dependencies]
a13n-harness = "==0.43.9"
a13n-stream-protocol = "==0.43.9"
a13n-logging = ">=0.2.0,<0.3.0"
"""
DOC = """\
| Harness UI                   | Harness, Stream Protocol | `==0.43.9`, identical for both |
| Service                      | Harness, Stream Protocol | `==0.43.9`, identical for both |
"""


@pytest.mark.parametrize(
    "after",
    [
        MANIFEST.replace("0.43.9", "0.43.8"),
        MANIFEST.replace("0.43.9", "0.44.0"),
        MANIFEST.replace("0.43.9", "1.43.10"),
        MANIFEST.replace("0.43.9", "0.43.10rc1"),
        MANIFEST.replace("0.43.9", "0.43.10", 1),
        MANIFEST.replace("0.43.9", "0.43.10").replace("0.3.0", "0.4.0"),
        MANIFEST.replace("0.43.9", "0.43.10").replace('version = "0.0.0"', 'version = "0.0.1"'),
        MANIFEST.replace("0.43.9", "0.43.10").replace('dependencies = ["a13n-harness"', 'dependencies = ["extra"'),
    ],
)
def test_non_patch_or_mixed_manifest_changes_are_not_eligible(after: str) -> None:
    assert patch_bump(MANIFEST, after) is None


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--initial-branch=main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "core.filemode", "false")  # Set the mode explicitly in the index for the mode test.
    for component in (UI, SERVICE):
        path = tmp_path / "packages" / component / "pyproject.toml"
        path.parent.mkdir(parents=True)
        path.write_text(MANIFEST, encoding="utf-8")
    doc = tmp_path / COMPATIBILITY_DOC
    doc.parent.mkdir()
    doc.write_text(DOC, encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "switch", "-c", "pr")
    return tmp_path


@pytest.mark.parametrize(
    ("consumers", "update_docs", "extra_change", "expected"),
    [
        ((UI,), False, None, (UI,)),
        ((SERVICE,), True, None, (SERVICE,)),
        ((UI, SERVICE), True, None, (UI, SERVICE)),
        ((UI, SERVICE), True, "scripts/test.py", ()),
        ((UI, SERVICE), True, "uv.lock", ()),
        ((UI, SERVICE), True, ".github/workflows/ci-automation.yml", ()),
        ((UI, SERVICE), True, COMPATIBILITY_DOC, ()),
        ((UI, SERVICE), True, "mode", ()),
        ((), False, COMPATIBILITY_DOC, ()),
    ],
)
def test_classifier_routes_the_merge_tree(
    repository: Path,
    consumers: tuple[str, ...],
    update_docs: bool,
    extra_change: str | None,
    expected: tuple[str, ...],
) -> None:
    for consumer in consumers:
        manifest = repository / "packages" / consumer / "pyproject.toml"
        manifest.write_text(MANIFEST.replace("0.43.9", "0.43.10"), encoding="utf-8")
    if update_docs:
        doc = repository / COMPATIBILITY_DOC
        rows = zip((UI, SERVICE), DOC.splitlines(keepends=True), strict=True)
        doc.write_text(
            "".join(row.replace("0.43.9", "0.43.10") if consumer in consumers else row for consumer, row in rows),
            encoding="utf-8",
        )
    if extra_change is not None and extra_change != "mode":
        file = repository / extra_change
        file.parent.mkdir(exist_ok=True, parents=True)
        with file.open("a", encoding="utf-8") as output:
            output.write("another change\n")
    git(repository, "add", ".")
    if extra_change == "mode":
        git(repository, "add", "--chmod=+x", "packages/a13n-service/pyproject.toml")
    git(repository, "commit", "-m", "update pins")
    assert classify_changes(repository) == ()  # An ordinary commit cannot activate the PR fast path.
    git(repository, "switch", "main")
    git(repository, "merge", "--no-ff", "pr", "-m", "PR test merge")
    assert classify_changes(repository) == expected
    # Main uses its whole push range, independently of how commits reached it.
    assert classify_changes(repository, git(repository, "rev-parse", "HEAD^1").strip()) == expected


@pytest.mark.parametrize(
    ("workflow", "filter_id"),
    [
        ("ci-a13n-harness-ui.yml", "filter"),
        ("ci-a13n-service.yml", "filter"),
        ("ci-a13n-service-e2e.yml", "paths"),
        ("ci-containers.yml", "filter"),
        ("images.yml", "filter"),
    ],
)
def test_expensive_workflows_use_the_same_classifier(workflow: str, filter_id: str) -> None:
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    steps = document["jobs"]["changes"]["steps"]
    by_id = {step["id"]: step for step in steps if "id" in step}
    assert by_id["harness-pins"]["uses"] == "./.github/actions/classify-harness-pins"
    assert "steps.harness-pins.outputs.components == ''" in by_id[filter_id]["if"]


def test_release_tooling_runs_the_published_pin_smoke_on_the_same_classification() -> None:
    workflow = ROOT / ".github/workflows/ci-automation.yml"
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    assert "packages/*/pyproject.toml" in document[True]["pull_request"]["paths"]
    steps = document["jobs"]["release-tooling"]["steps"]
    assert any(step.get("id") == "harness-pins" for step in steps)
    smoke = steps[-1]
    assert smoke["if"] == "steps.harness-pins.outputs.components != ''"
    assert "prepare-release-version.py" in smoke["run"]
    assert "uv pip install --no-sources" in smoke["run"]


def test_main_push_includes_earlier_runtime_changes(repository: Path) -> None:
    before = git(repository, "rev-parse", "HEAD").strip()
    runtime = repository / "runtime.py"
    runtime.write_text("pass\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "change runtime")
    manifest = repository / "packages" / SERVICE / "pyproject.toml"
    manifest.write_text(MANIFEST.replace("0.43.9", "0.43.10"), encoding="utf-8")
    git(repository, "commit", "-am", "update pin")
    assert classify_changes(repository, before) == ()
    assert classify_changes(repository, git(repository, "rev-parse", "HEAD^1").strip()) == (SERVICE,)
    assert classify_changes(repository, "0" * 40) == ()


@pytest.mark.parametrize(
    "workflow",
    [
        "ci-a13n-harness-ui.yml",
        "ci-a13n-service.yml",
        "ci-a13n-service-e2e.yml",
        "ci-containers.yml",
        "ci-automation.yml",
    ],
)
def test_main_pin_push_cannot_cancel_previous_ci(workflow: str) -> None:
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    assert "github.event_name == 'push' && github.sha" in document["concurrency"]["group"]


def test_main_pin_push_skips_e2e_deployment_and_sandbox_resolution() -> None:
    document = yaml.safe_load((ROOT / ".github/workflows/ci-a13n-service-e2e.yml").read_text(encoding="utf-8"))
    job = document["jobs"]["changes"]
    assert "steps.harness-pins.outputs.components == ''" in job["outputs"]["deploy"]
    sandbox = next(step for step in job["steps"] if step.get("id") == "sandbox")
    assert "steps.harness-pins.outputs.components == ''" in sandbox["if"]
