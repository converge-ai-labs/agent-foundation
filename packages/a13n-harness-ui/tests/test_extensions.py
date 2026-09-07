from __future__ import annotations

import importlib.metadata

import pytest
from a13n_harness.capabilities import WebCapability
from a13n_harness.plugin_factories import HarnessPluginFactory
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.extensions import catalog as catalog_module


class _PluginFactory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls) -> str:
        return "vendor.duplicate"

    def validate_configuration(self, configuration):  # type: ignore[no-untyped-def]
        raise AssertionError("ambiguous factory must not be selected")

    def create_plugin(self, context):  # type: ignore[no-untyped-def]
        raise AssertionError("ambiguous factory must not be selected")


def test_catalog_marks_builtin_and_host_collision_ambiguous() -> None:
    catalog = HarnessUiExtensionCatalog(host_capabilities={"web": WebCapability})

    references = [item for item in catalog.references if item.kind == "capability" and item.key == "web"]

    assert len(references) == 2
    assert all(item.configurable is False for item in references)
    with pytest.raises(CompositionError) as error:
        catalog.capabilities((("web", {}),))
    assert error.value.code == "extension_catalog_ambiguous"


def test_catalog_marks_host_to_host_factory_collision_ambiguous() -> None:
    catalog = HarnessUiExtensionCatalog(host_plugin_factories=(_PluginFactory(), _PluginFactory()))

    references = [
        item for item in catalog.references if item.kind == "harness_plugin" and item.key == "vendor.duplicate"
    ]

    assert len(references) == 2
    assert all(item.configurable is False for item in references)
    with pytest.raises(CompositionError) as error:
        catalog.plugin_catalog(("vendor.duplicate",))
    assert error.value.code == "extension_catalog_ambiguous"


def test_catalog_refresh_failure_retains_previous_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = HarnessUiExtensionCatalog()
    previous = catalog.references
    entry = importlib.metadata.EntryPoint(
        name="vendor.new",
        value="vendor.module:NewCapability",
        group="a13n_harness_ui.capabilities",
    )
    monkeypatch.setattr(
        catalog_module,
        "_capability_entry_points",
        lambda: {"vendor.new": (entry,)},
    )

    def fail_provider_discovery():  # type: ignore[no-untyped-def]
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(
        catalog_module,
        "discover_environment_provider_references",
        fail_provider_discovery,
    )

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        catalog.refresh()

    assert catalog.references == previous
    with pytest.raises(CompositionError) as error:
        catalog.capabilities((("vendor.new", {}),))
    assert error.value.code == "capability_catalog_invalid"


def test_catalog_projection_returns_detached_models() -> None:
    catalog = HarnessUiExtensionCatalog()

    first = catalog.references
    second = catalog.references

    assert first == second
    assert first is not second
    assert all(left is not right for left, right in zip(first, second, strict=True))
