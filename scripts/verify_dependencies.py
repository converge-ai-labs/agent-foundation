"""Test inputs invisible to Python imports (paths read, loaded or executed by tests).

Patterns are repository-relative fnmatch patterns; all matches are additive.
Keep ordinary imports in PythonGraph, and add entries here when a tooling test
reads a file or launches a program instead of importing it.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

TEST_INPUTS: dict[str, tuple[str, ...]] = {
    "test_pr_change_breakdown.py": (
        ".github/scripts/pr-change-*.cjs",
        ".github/workflows/pr-change-breakdown.yml",
        ".github/workflows/ci-automation.yml",
        "proto/a13n-envd/eip/v1/artifacts/generated-files.json",
    ),
    "test_pr_labels.py": (".github/workflows/*.yml",),
    "test_ci_inputs.py": (".github/workflows/ci-*.yml",),
    "test_envd_ci_workflow.py": (".github/workflows/ci-a13n-envd.yml",),
    "test_envd_release_workflow.py": (".github/workflows/release-a13n-envd.yml",),
    "test_harness_ui_ci_workflow.py": (
        ".github/workflows/ci-a13n-harness-ui*.yml",
        ".github/workflows/images.yml",
    ),
    "test_harness_ui_release_workflow.py": (
        ".github/workflows/release-a13n-harness.yml",
        ".github/workflows/release-a13n-harness-ui.yml",
    ),
    "test_image_workflows.py": (
        ".github/workflows/images.yml",
        ".github/workflows/release-*.yml",
        ".github/workflows/ci-containers.yml",
        "deploy/containers/*",
    ),
    "test_service_contract_notification.py": (".github/workflows/notify-service-contract.yml",),
    "test_local_validation.py": (
        ".github/workflows/ci-automation.yml",
        ".github/workflows/ci-a13n-service.yml",
        "Makefile",
        ".pre-commit-config.yaml",
    ),
    "test_release_notes.py": ("scripts/create-github-release.py", ".github/release-notes/*"),
    "test_check_release_version.py": ("scripts/check-release-version.py",),
    "test_prepare_release_version.py": ("scripts/prepare-release-version.py", "scripts/check-release-version.py"),
    "test_check_a13n_distributions.py": ("scripts/check-a13n-distributions.py",),
    "test_check_a13n_harness_ui_distribution.py": ("scripts/check-a13n-harness-ui-distribution.py",),
    "test_service_contract.py": ("scripts/export-a13n-service-openapi.py",),
    "test_install_envd.py": ("scripts/install-a13n-envd.sh",),
    "test_docs.py": (
        "scripts/docs/*.py",
        "scripts/docs/*.css",
        "scripts/docs/*.json",
        "scripts/docs/theme/*",
        "mkdocs.yml",
        "docs/*.md",
    ),
    "provider_smoke/test_composio.py": ("scripts/provider-smoke/composio.py", "scripts/provider-smoke/common.py"),
    "provider_smoke/test_openrouter.py": ("scripts/provider-smoke/openrouter.py", "scripts/provider-smoke/common.py"),
}


def tests_for(path: str, root: Path) -> set[Path]:
    """Existing tests declaring this input, including inputs deleted from the checkout."""
    return {
        root / "scripts/tests" / test
        for test, patterns in TEST_INPUTS.items()
        if any(fnmatchcase(path, pattern) for pattern in patterns) and (root / "scripts/tests" / test).is_file()
    }
