from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from a13n_environment_provider import (
    EnvironmentProviderError,
    EnvironmentProviderSpec,
    build_environment_provider_factory_catalog,
    discover_environment_provider_factory_references,
)

from a13n_plugin_examples.demo_environment import (
    PROVIDER_KEY,
    run_environment_code_demo,
    run_environment_entrypoint_demo,
)

PLUGIN_MODULE = "a13n_plugin_examples.environment"


def _workspace_roots(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    docs = tmp_path / "docs"
    source.mkdir()
    docs.mkdir()
    (source / "message.txt").write_text("source workspace\n", encoding="utf-8")
    (docs / "message.txt").write_text("documentation workspace\n", encoding="utf-8")
    return source, docs


def test_environment_entrypoint_metadata_is_lazy_and_selection_is_explicit(tmp_path: Path) -> None:
    assert PLUGIN_MODULE not in sys.modules

    references = discover_environment_provider_factory_references()
    assert PROVIDER_KEY in {reference.provider_key for reference in references}
    assert PLUGIN_MODULE not in sys.modules

    catalog = build_environment_provider_factory_catalog(extension_keys=(PROVIDER_KEY,))
    assert PLUGIN_MODULE in sys.modules
    registration = catalog.registrations[0]
    assert registration.provider_key == PROVIDER_KEY
    assert registration.import_target == ("a13n_plugin_examples.environment:WorkspaceEnvironmentProviderFactory")

    from a13n_plugin_examples.environment import WorkspaceEnvironmentRuntime

    manager = catalog.create_manager(
        EnvironmentProviderSpec(
            provider_key=PROVIDER_KEY,
            schema_version="1",
            parameters={
                "root": str(tmp_path / "not-created-by-the-factory"),
                "environment_id": "workspace-inert",
            },
        ),
        runtime=WorkspaceEnvironmentRuntime(),
    )
    assert manager.lifecycle_capabilities.resource_allocation.value == "single_from_spec"
    assert not (tmp_path / "not-created-by-the-factory").exists()


def test_environment_explicit_factory_needs_no_metadata_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from a13n_plugin_examples.environment import WorkspaceEnvironmentProviderFactory

    monkeypatch.setattr(
        "a13n_environment_provider.factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("explicit mode must not scan metadata")),
    )
    catalog = build_environment_provider_factory_catalog(explicit_factories=(WorkspaceEnvironmentProviderFactory(),))

    assert catalog.registrations[0].import_target is None
    from a13n_plugin_examples.environment import WorkspaceEnvironmentRuntime

    manager = catalog.create_manager(
        EnvironmentProviderSpec(
            provider_key=PROVIDER_KEY,
            schema_version="1",
            parameters={
                "root": str(tmp_path / "still-inert"),
                "environment_id": "workspace-code",
            },
        ),
        runtime=WorkspaceEnvironmentRuntime(),
    )
    assert manager.lifecycle_capabilities.attachment_concurrency.value == "shared"
    assert not (tmp_path / "still-inert").exists()


def test_environment_provider_factory_rejects_invalid_json_configuration() -> None:
    catalog = build_environment_provider_factory_catalog(extension_keys=(PROVIDER_KEY,))

    from a13n_plugin_examples.environment import WorkspaceEnvironmentRuntime

    with pytest.raises(EnvironmentProviderError) as exc_info:
        catalog.create_manager(
            EnvironmentProviderSpec(
                provider_key=PROVIDER_KEY,
                schema_version="1",
                parameters={"environment_id": "missing-root"},
            ),
            runtime=WorkspaceEnvironmentRuntime(),
        )

    assert exc_info.value.code == "provider_spec_invalid"


def test_environment_entrypoint_demo_routes_two_bindings(tmp_path: Path) -> None:
    source, docs = _workspace_roots(tmp_path)

    result = asyncio.run(run_environment_entrypoint_demo(source_root=source, docs_root=docs))

    assert result.selection_mode == "entrypoint"
    assert result.provider_key == PROVIDER_KEY
    assert result.default_text == "source workspace\n"
    assert result.docs_text == "documentation workspace\n"
    assert result.aliases == ("source", "docs")


def test_environment_code_demo_routes_two_bindings(tmp_path: Path) -> None:
    source, docs = _workspace_roots(tmp_path)

    result = asyncio.run(run_environment_code_demo(source_root=source, docs_root=docs))

    assert result.selection_mode == "code"
    assert result.provider_key == PROVIDER_KEY
    assert result.default_text == "source workspace\n"
    assert result.docs_text == "documentation workspace\n"
    assert result.aliases == ("source", "docs")
