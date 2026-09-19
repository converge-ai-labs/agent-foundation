from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from a13n_harness.providers.catalog import ProviderCatalog, ProviderNotSelected
from a13n_harness.providers.environment.errors import EnvironmentProviderError

from a13n_plugin_examples.demo_environment import (
    PROVIDER_TYPE,
    installed_environment_catalog,
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


def test_environment_entrypoint_loading_is_explicit_and_construction_is_inert(tmp_path: Path) -> None:
    assert PLUGIN_MODULE not in sys.modules

    catalog = installed_environment_catalog()

    assert PLUGIN_MODULE in sys.modules
    assert tuple(catalog) == (PROVIDER_TYPE,)
    provider = catalog.require(PROVIDER_TYPE)
    root = tmp_path / "not-created-by-the-provider"
    configuration = provider.validate_environment({"root": str(root)})
    environment = provider.construct(
        environment_id="workspace-inert", configuration=configuration, state=None, runtime=None
    )
    assert environment.provider_key == PROVIDER_TYPE
    assert not root.exists()


def test_environment_explicit_definition_needs_no_metadata_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from a13n_plugin_examples.environment import WORKSPACE_ENVIRONMENT

    monkeypatch.setattr(
        "a13n_harness.providers.plugins.importlib.metadata.entry_points",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("explicit mode must not scan metadata")),
    )
    catalog = ProviderCatalog((WORKSPACE_ENVIRONMENT,))
    provider = catalog.require(PROVIDER_TYPE)
    root = tmp_path / "still-inert"
    configuration = provider.validate_environment({"root": str(root)})
    provider.construct(configuration=configuration, environment_id="workspace-code", state=None, runtime=None)

    assert tuple(catalog) == (PROVIDER_TYPE,)
    assert not root.exists()


def test_environment_provider_rejects_invalid_json_configuration() -> None:
    provider = installed_environment_catalog().require(PROVIDER_TYPE)

    with pytest.raises(EnvironmentProviderError) as exc_info:
        provider.validate_environment({"environment_id": "missing-root"})

    assert exc_info.value.code == "provider_spec_invalid"


def test_missing_provider_selection_is_a_safe_configuration_error() -> None:
    with pytest.raises(ProviderNotSelected, match=PROVIDER_TYPE):
        ProviderCatalog(()).require(PROVIDER_TYPE)


def test_environment_entrypoint_demo_routes_two_fresh_environments(tmp_path: Path) -> None:
    source, docs = _workspace_roots(tmp_path)

    result = asyncio.run(run_environment_entrypoint_demo(source_root=source, docs_root=docs))

    assert result.selection_mode == "entrypoint"
    assert result.provider_key == PROVIDER_TYPE
    assert result.default_text == "source workspace\n"
    assert result.docs_text == "documentation workspace\n"
    assert result.aliases == ("source", "docs")
    assert result.exported_state_aliases == ()
    assert result.roots_preserved


def test_environment_code_demo_routes_two_fresh_environments(tmp_path: Path) -> None:
    source, docs = _workspace_roots(tmp_path)

    result = asyncio.run(run_environment_code_demo(source_root=source, docs_root=docs))

    assert result.selection_mode == "code"
    assert result.provider_key == PROVIDER_TYPE
    assert result.default_text == "source workspace\n"
    assert result.docs_text == "documentation workspace\n"
    assert result.aliases == ("source", "docs")
    assert result.exported_state_aliases == ()
    assert result.roots_preserved
