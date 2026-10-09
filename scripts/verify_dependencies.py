"""Test inputs invisible to Python imports (paths read, loaded or executed by tests).

Patterns are repository-relative fnmatch patterns; all matches are additive.
Keep ordinary imports in PythonGraph, and add entries here when a tooling test
reads a file or launches a program instead of importing it.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import Path

TEST_INPUTS: dict[str, tuple[str, ...]] = {
    "packages/a13n-service/tests/test_documentation.py": (
        "docs/a13n-service/*",
        "packages/a13n-service/build_docs.py",
        "packages/a13n-service/hatch_build.py",
        "packages/a13n-service/pyproject.toml",
    ),
    "packages/a13n-service/tests/test_migrations.py": ("packages/a13n-service/a13n_service/migrations/*",),
    "packages/a13n-harness-ui/tests/test_coordinator_migration.py": (
        "packages/a13n-harness-ui/a13n_harness_ui/storage/migrations/*",
    ),
    "frontend/packages/a13n-ui/tests/license-build.test.ts": (
        "frontend/*/*/vite.config.ts",
        "frontend/packages/a13n-ui/vite.ts",
        "frontend/packages/a13n-ui/LICENSE.coss",
        "frontend/packages/a13n-ui/package.json",
    ),
    "test_observability_tooling.py": ("dev/observability/*",),
    "test_mcp_apps_demo.py": ("dev/harness-ui/mcp_apps.py",),
    "test_service_e2e_tooling.py": ("e2e/service/*", "dev/fixtures/*"),
    "test_pr_change_breakdown.py": (
        ".github/scripts/pr-change-*.cjs",
        ".github/workflows/pr-change-breakdown.yml",
        ".github/workflows/ci-automation.yml",
        "proto/a13n-envd/eip/v1/artifacts/generated-files.json",
    ),
    "test_pr_labels.py": (".github/workflows/*.yml",),
    "test_ci_inputs.py": (".github/workflows/ci-*.yml", ".github/workflows/images.yml"),
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
        "deploy/docker/images/*",
        "deploy/docker/compose/*",
    ),
    "test_k8s_local.py": ("deploy/kubernetes/*",),
    "test_deploy_smoke.py": ("Makefile",),
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
    "test_check_a13n_harness_ui_distribution.py": (
        "scripts/check-a13n-harness-ui-distribution.py",
        "frontend/packages/a13n-ui/LICENSE.coss",
    ),
    "test_service_contract.py": ("scripts/export-a13n-service-openapi.py",),
    "test_install_envd.py": ("scripts/install-a13n-envd.sh",),
    "test_docs.py": ("scripts/docs/*.py", "scripts/docs/*.json", "docs/*"),
    "provider_smoke/test_composio.py": ("scripts/provider-smoke/composio.py", "scripts/provider-smoke/common.py"),
    "provider_smoke/test_openrouter.py": ("scripts/provider-smoke/openrouter.py", "scripts/provider-smoke/common.py"),
}


def tests_for(path: str, root: Path) -> set[Path]:
    """Existing tests declaring this input, including inputs deleted from the checkout."""
    return {
        target
        for test, patterns in TEST_INPUTS.items()
        if any(fnmatchcase(path, pattern) for pattern in patterns)
        for target in [root / test if test.startswith(("packages/", "frontend/")) else root / "scripts/tests" / test]
        if target.is_file()
    }


def inputs_for(scopes: Iterable[str]) -> set[str]:
    """Non-import inputs of tests within the selected file/directory scopes."""
    scopes = tuple(scope.split("::", 1)[0] for scope in scopes)
    return {
        pattern
        for test, patterns in TEST_INPUTS.items()
        for target in [test if test.startswith(("packages/", "frontend/")) else f"scripts/tests/{test}"]
        if any(target == scope or target.startswith(scope + "/") for scope in scopes)
        for pattern in patterns
    }
