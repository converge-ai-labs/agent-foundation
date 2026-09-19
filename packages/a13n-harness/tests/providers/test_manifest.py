"""One manifest contract for all five domains: inert selection and one catalog owner."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from a13n_harness.providers.catalog import ProviderCatalog, ProviderNotSelected
from a13n_harness.providers.connector.builtins import COMPOSIO
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_harness.providers.memory.builtins import MEM0_OSS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.plugins import ProviderManifest, load_provider_plugins
from a13n_harness.providers.web.builtins import built_in_web_providers

DOMAINS = [
    pytest.param("web", built_in_web_providers()[0], id="web"),
    pytest.param("model", BUILT_IN_MODEL_PROVIDERS[0], id="model"),
    pytest.param("memory", MEM0_OSS, id="memory"),
    pytest.param("connector", COMPOSIO, id="connector"),
    pytest.param("environment", BUILT_IN_ENVIRONMENT_PROVIDERS[0], id="environment"),
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


@pytest.mark.parametrize(("field", "builtin"), DOMAINS)
def test_selection_is_inert_and_imports_only_chosen_plugins(monkeypatch, field, builtin):
    definition = replace(builtin, type="custom_provider")
    manifest = ProviderManifest(api_version=1, **{field: (definition,)})
    loaded: list[str] = []
    monkeypatch.setattr(
        "a13n_harness.providers.plugins.importlib.metadata.entry_points", _entry_points(manifest, loaded)
    )

    assert load_provider_plugins(()) == ()
    assert loaded == []

    selected = load_provider_plugins(("chosen",))
    assert loaded == ["chosen"]
    catalog = ProviderCatalog(getattr(selected[0].manifest, field))
    assert catalog["custom_provider"] is definition
    with pytest.raises(TypeError):
        catalog["custom_provider"] = definition


@pytest.mark.parametrize(("field", "builtin"), DOMAINS)
def test_the_catalog_is_the_single_owner_of_one_type_per_domain(field, builtin):
    assert ProviderManifest(api_version=1, **{field: (builtin, builtin)})
    with pytest.raises(ValueError, match="duplicate"):
        ProviderCatalog((builtin, builtin))
    with pytest.raises(ProviderNotSelected):
        ProviderCatalog((builtin,)).require("absent_provider")


@pytest.mark.parametrize(("field", "builtin"), DOMAINS)
def test_manifest_rejects_foreign_definitions_and_unsupported_api_versions(field, builtin):
    with pytest.raises(ValueError, match="API version"):
        ProviderManifest(api_version=2, **{field: (builtin,)})
    with pytest.raises(TypeError, match="immutable tuple"):
        ProviderManifest(api_version=1, **{field: [builtin]})
