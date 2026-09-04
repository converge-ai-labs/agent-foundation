"""Regression coverage for package import boundaries."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "modules",
    [
        ("a13n_service.agents", "a13n_service.connectivity.ingress.domain"),
        ("a13n_service.connectivity.ingress.domain", "a13n_service.agents"),
    ],
)
def test_agents_and_connectivity_import_in_either_order(modules: tuple[str, str]) -> None:
    script = "; ".join(f"import {module}" for module in modules)
    subprocess.run([sys.executable, "-c", script], check=True)


def test_agents_package_preserves_runtime_exports() -> None:
    from a13n_service.agents import AgentInvocationResolver, AgentReconstructor

    assert AgentInvocationResolver is not None
    assert AgentReconstructor is not None
