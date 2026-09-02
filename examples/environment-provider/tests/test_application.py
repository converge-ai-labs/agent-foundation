from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironment,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)

from a13n_environment_provider_example import application as application_module
from a13n_environment_provider_example import run_direct_local


class _CloseFailingEnvironment(DirectLocalEnvironment):
    async def _close(self) -> None:
        raise RuntimeError("close failed")


def test_direct_local_example_runs_offline_and_preserves_host_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    result = asyncio.run(run_direct_local(workspace))

    assert result.provider_key == "a13n.direct-local"
    assert result.environment_id == "direct-local-example"
    assert result.text == "hello from a13n.direct-local\n"
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
        DirectLocalProviderConfiguration(
            environment_id="failure-test",
            root=DirectLocalRootConfiguration(path=tmp_path),
        )
    )

    async def fail_use() -> None:
        raise ValueError("use failed")

    with pytest.raises(BaseExceptionGroup) as captured:
        asyncio.run(application_module._run_and_close(environment, fail_use))

    assert [type(error) for error in captured.value.exceptions] == [ValueError, RuntimeError]
