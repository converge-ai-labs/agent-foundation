"""Regression coverage for package import boundaries."""

import subprocess
import sys


def test_agents_and_connectivity_import_in_either_order() -> None:
    modules = ("a13n_service.agents", "a13n_service.connectivity.accounts.reception")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", "; ".join(f"import {module}" for module in order)], stderr=subprocess.PIPE
        )
        for order in (modules, modules[::-1])
    ]
    failures = [(process.args, process.communicate()[1]) for process in processes if process.wait() != 0]
    assert not failures, failures


def test_agents_package_preserves_runtime_exports() -> None:
    from a13n_service.agents import AgentInvocationResolver, AgentManagement, AgentReconstructor

    assert AgentInvocationResolver is not None
    assert AgentManagement is not None
    assert AgentReconstructor is not None
