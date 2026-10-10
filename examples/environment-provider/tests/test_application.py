from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from a13n_environment_example import application as application_module
from a13n_environment_example import run_direct_local


def test_direct_local_example_runs_offline_and_preserves_host_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    result = asyncio.run(run_direct_local(workspace))

    assert result.provider_key == "direct_local"
    assert result.environment_id == "direct-local-example"
    assert result.text == "hello from direct_local\n"
    assert result.workspace == workspace.resolve()
    assert result.workspace_preserved
    assert result.state is None
    assert (workspace / "provider-example.txt").read_text(encoding="utf-8") == result.text


def test_direct_local_example_constructs_a_fresh_stateless_adapter_each_time(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    first = asyncio.run(run_direct_local(workspace))
    second = asyncio.run(run_direct_local(workspace))

    assert first.state is None
    assert second.state is None
    assert second.text == first.text
    assert workspace.is_dir()


def test_target_use_and_cleanup_failures_are_preserved():
    async def fail_use():
        raise ValueError("use failed")

    async def fail_cleanup():
        raise RuntimeError("cleanup failed")

    with pytest.raises(BaseExceptionGroup) as captured:
        asyncio.run(application_module._run_with_cleanup(fail_use, fail_cleanup))
    assert [type(error) for error in captured.value.exceptions] == [ValueError, RuntimeError]


def test_local_envd_example_closes_host_runtime_and_preserves_workspace(tmp_path: Path) -> None:
    import os

    configured = os.environ.get("A13N_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("A13N_ENVD_TEST_BINARY enables the real Local Device example")
    workspace = tmp_path / "workspace"
    result = asyncio.run(application_module.run_local_envd(workspace, executable=Path(configured)))
    assert result.provider_key == "local_envd"
    assert result.state is None and result.workspace_preserved
    assert result.text == "hello from local_envd\n"
    assert (workspace / "provider-example.txt").read_text() == result.text


def test_docker_partial_creation_is_cleaned_up(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_environment.errors import EnvironmentManagementError, EnvironmentProviderErrorCategory, provider_error
    from a13n_environment.models import EnvironmentState

    state = EnvironmentState(provider_key="docker", state_version="1", state={"container_id": "allocated"})
    failure = provider_error("docker", "provider_unknown_outcome", EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME)
    provider = AsyncMock()
    provider.__aenter__.return_value = provider
    provider.create.side_effect = EnvironmentManagementError(failure, state, "op-create", "docker-example")
    definition = SimpleNamespace(
        validate_environment=lambda value: value, open_provider=AsyncMock(return_value=provider)
    )
    monkeypatch.setattr(application_module, "DOCKER", definition)
    with pytest.raises(EnvironmentManagementError):
        asyncio.run(application_module.run_docker())
    provider.destroy.assert_awaited_once_with(
        {"image": application_module.DEFAULT_EXAMPLE_DOCKER_IMAGE},
        environment_id="docker-example",
        state=state,
        operation_id="op-delete",
    )
    provider.__aexit__.assert_awaited_once()
