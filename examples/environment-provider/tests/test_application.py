from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.environment.direct_local.provider import DirectLocalEnvironment

from a13n_environment_example import application as application_module
from a13n_environment_example import run_direct_local


class _CloseFailingEnvironment(DirectLocalEnvironment):
    async def _close(self) -> None:
        raise RuntimeError("close failed")


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


def test_run_and_close_preserves_use_and_cleanup_failures(tmp_path: Path) -> None:
    environment = _CloseFailingEnvironment(
        DirectLocalEnvironmentConfiguration(
            root=DirectLocalRootConfiguration(path=tmp_path),
        ),
        environment_id="failure-test",
    )

    async def fail_use() -> None:
        raise ValueError("use failed")

    with pytest.raises(BaseExceptionGroup) as captured:
        asyncio.run(application_module._run_and_close(environment, fail_use))

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


@pytest.mark.parametrize("close_fails", [False, True])
def test_docker_example_closes_borrowed_runtime_when_creation_fails(monkeypatch, close_fails: bool) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    engine = AsyncMock()
    if close_fails:
        engine.close.side_effect = OSError("engine close failed")
    provider = AsyncMock()
    provider.validate_environment = lambda recipe: recipe
    provider.create.side_effect = RuntimeError("creation failed")
    monkeypatch.setattr(application_module, "select_builtin_environment_providers", lambda keys: ())
    monkeypatch.setattr(
        application_module, "ProviderCatalog", lambda definitions: SimpleNamespace(require=lambda key: provider)
    )
    monkeypatch.setattr(application_module.DockerSDKEngine, "connect", lambda endpoint: engine)

    if close_fails:
        with pytest.raises(BaseExceptionGroup) as captured:
            asyncio.run(application_module.run_docker())
        assert [type(error) for error in captured.value.exceptions] == [RuntimeError, OSError]
    else:
        with pytest.raises(RuntimeError, match="creation failed"):
            asyncio.run(application_module.run_docker())
    engine.close.assert_awaited_once()
