from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_harness import RunError
from a13n_harness.environment import (
    EnvironmentError,
    EnvironmentRunExtensionFactoryContext,
    build_environment_run_extension_factory_catalog,
    discover_environment_run_extension_factory_references,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from a13n_plugin_examples.demo_environment_extension import (
    EXTENSION_KEY,
    run_environment_extension_code_demo,
    run_environment_extension_entrypoint_demo,
)

EXTENSION_MODULE = "a13n_plugin_examples.environment_extension"


def test_environment_extension_metadata_is_lazy_and_selection_is_explicit() -> None:
    assert EXTENSION_MODULE not in sys.modules

    references = discover_environment_run_extension_factory_references()
    assert EXTENSION_KEY in {reference.extension_key for reference in references}
    assert EXTENSION_MODULE not in sys.modules

    catalog = build_environment_run_extension_factory_catalog(extension_keys=(EXTENSION_KEY,))
    assert EXTENSION_MODULE in sys.modules
    registration = catalog.registrations[0]
    assert registration.extension_key == EXTENSION_KEY
    assert registration.import_target == ("a13n_plugin_examples.environment_extension:WorkspaceMarkerExtensionFactory")


def test_environment_extension_explicit_factory_needs_no_metadata_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from a13n_plugin_examples.environment_extension import WorkspaceMarkerExtensionFactory

    monkeypatch.setattr(
        "a13n_harness.environment.extension_factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("explicit mode must not scan metadata")),
    )
    catalog = build_environment_run_extension_factory_catalog(explicit_factories=(WorkspaceMarkerExtensionFactory(),))

    extension = catalog.create_extension(
        EnvironmentRunExtensionFactoryContext(
            extension_key=EXTENSION_KEY,
            extension_id="marker-code",
            configuration={},
        )
    )
    assert extension.extension_id == "marker-code"
    assert catalog.registrations[0].import_target is None


def test_environment_extension_factory_rejects_invalid_configuration() -> None:
    catalog = build_environment_run_extension_factory_catalog(extension_keys=(EXTENSION_KEY,))

    with pytest.raises(EnvironmentError) as exc_info:
        catalog.create_extension(
            EnvironmentRunExtensionFactoryContext(
                extension_key=EXTENSION_KEY,
                extension_id="marker-invalid",
                configuration={"marker_path": "/outside-workspace"},
            )
        )

    assert exc_info.value.code == "environment_extension_factory_failed"
    assert exc_info.value.__cause__ is None


def test_environment_extension_entrypoint_demo_runs_complete_scope(tmp_path: Path) -> None:
    result = asyncio.run(run_environment_extension_entrypoint_demo(workspace_root=tmp_path))

    assert result.selection_mode == "entrypoint"
    assert result.extension_key == EXTENSION_KEY
    assert result.extension_id == "marker-entrypoint"
    assert result.marker_text == f"entrypoint:{result.run_id}\n"
    assert result.marker_removed is True


def test_environment_extension_code_demo_runs_complete_scope(tmp_path: Path) -> None:
    result = asyncio.run(run_environment_extension_code_demo(workspace_root=tmp_path))

    assert result.selection_mode == "code"
    assert result.extension_key == EXTENSION_KEY
    assert result.extension_id == "marker-code"
    assert result.marker_text == f"code:{result.run_id}\n"
    assert result.marker_removed is True


def test_environment_extension_demo_closes_adapter_after_run_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_model() -> FunctionModel:
        async def stream(
            messages: list[ModelMessage],
            info: AgentInfo,
        ) -> AsyncIterator[str]:
            del messages, info
            raise RuntimeError("expected model failure")
            yield "unreachable"

        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(
        "a13n_plugin_examples.demo_environment_extension._offline_model",
        failing_model,
    )

    with pytest.raises(RunError):
        asyncio.run(run_environment_extension_code_demo(workspace_root=tmp_path))

    assert not (tmp_path / ".example-run").exists()
    assert tmp_path.is_dir()
