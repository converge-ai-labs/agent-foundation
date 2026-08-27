from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "check-agent-distributions.py"
INTERNAL_REQUIREMENTS = (
    "a13n-environment-provider",
    "a13n-harness",
)


def load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_agent_distributions", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("package_name", INTERNAL_REQUIREMENTS)
@pytest.mark.parametrize("version", ["1.2.3", "1.2.3rc4"])
def test_release_validation_requires_exact_same_version_internal_requirement(
    package_name: str,
    version: str,
) -> None:
    checker = load_checker()

    checker._validate_internal_requirement(
        [f"{package_name}=={version}", "pydantic>=2.12"],
        version,
        Path("package.whl"),
        package_name,
        require_exact_internal_version=True,
    )


@pytest.mark.parametrize("package_name", INTERNAL_REQUIREMENTS)
def test_development_validation_accepts_unpinned_workspace_requirement(package_name: str) -> None:
    checker = load_checker()

    checker._validate_internal_requirement(
        [package_name, "pydantic>=2.12"],
        "0.0.0",
        Path("package.whl"),
        package_name,
        require_exact_internal_version=False,
    )


@pytest.mark.parametrize("package_name", INTERNAL_REQUIREMENTS)
def test_release_validation_rejects_unpinned_requirement(package_name: str) -> None:
    checker = load_checker()

    with pytest.raises(checker.DistributionError, match=rf"{package_name}==1\.2\.3"):
        checker._validate_internal_requirement(
            [package_name],
            "1.2.3",
            Path("package.whl"),
            package_name,
            require_exact_internal_version=True,
        )


@pytest.mark.parametrize("package_name", INTERNAL_REQUIREMENTS)
def test_development_validation_rejects_published_exact_requirement(package_name: str) -> None:
    checker = load_checker()

    with pytest.raises(checker.DistributionError, match=rf"Expected {package_name} "):
        checker._validate_internal_requirement(
            [f"{package_name}==0.0.0"],
            "0.0.0",
            Path("package.whl"),
            package_name,
            require_exact_internal_version=False,
        )
