"""Installed manifests carry Environment definitions; one catalog per domain owns unique types."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from a13n_harness.providers.catalog import ProviderCatalog, ProviderNotSelected
from a13n_harness.providers.connector.builtins import COMPOSIO
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.plugins import ProviderManifest, load_provider_plugins
from a13n_harness.providers.web.builtins import built_in_web_providers

ENVIRONMENT = BUILT_IN_ENVIRONMENT_PROVIDERS[0]
DOMAINS = [
    pytest.param(built_in_web_providers()[0], id="web"),
    pytest.param(BUILT_IN_MODEL_PROVIDERS[0], id="model"),
    pytest.param(COMPOSIO, id="connector"),
    pytest.param(ENVIRONMENT, id="environment"),
]


def _entry_points(manifest: ProviderManifest, loaded: list[str]):
    def entries(*, group):
        assert group == "a13n_harness.providers.plugins"
        return [
            SimpleNamespace(name="unselected", load=lambda: pytest.fail("unselected plugin imported")),
            SimpleNamespace(
                name="chosen",
                dist=None,
                value="fixture:manifest",
                load=lambda: loaded.append("chosen") or manifest,
            ),
        ]

    return entries


def test_selection_is_inert_and_imports_only_chosen_plugins(monkeypatch):
    definition = replace(ENVIRONMENT, type="custom_provider")
    manifest = ProviderManifest(api_version=1, environment=(definition,))
    loaded: list[str] = []
    monkeypatch.setattr(
        "a13n_harness.providers.plugins.importlib.metadata.entry_points", _entry_points(manifest, loaded)
    )

    assert load_provider_plugins(()) == ()
    assert loaded == []

    selected = load_provider_plugins(("chosen",))
    assert loaded == ["chosen"]
    catalog = ProviderCatalog(selected[0].manifest.environment)
    assert catalog["custom_provider"] is definition
    with pytest.raises(TypeError):
        catalog["custom_provider"] = definition


@pytest.mark.parametrize("builtin", DOMAINS)
def test_the_catalog_is_the_single_owner_of_one_type_per_domain(builtin):
    with pytest.raises(ValueError, match="duplicate"):
        ProviderCatalog((builtin, builtin))
    with pytest.raises(ProviderNotSelected):
        ProviderCatalog((builtin,)).require("absent_provider")


def test_manifest_rejects_other_domains_and_unsupported_api_versions():
    assert ProviderManifest(api_version=1, environment=(ENVIRONMENT, ENVIRONMENT))
    with pytest.raises(ValueError, match="API version"):
        ProviderManifest(api_version=2, environment=(ENVIRONMENT,))
    with pytest.raises(TypeError, match="immutable tuple"):
        ProviderManifest(api_version=1, environment=[ENVIRONMENT])
    with pytest.raises(TypeError, match="immutable tuple"):
        ProviderManifest(api_version=1, environment=(BUILT_IN_MODEL_PROVIDERS[0],))
