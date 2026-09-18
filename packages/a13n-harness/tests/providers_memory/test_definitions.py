from dataclasses import replace
from types import SimpleNamespace

import pytest
from a13n_harness.providers.memory import MemoryProviderCatalog
from a13n_harness.providers.memory.builtins import MEM0_OSS
from a13n_harness.providers.memory.configuration import Mem0Credential, Mem0OSSConfiguration
from a13n_harness.providers.plugins import ProviderManifest, load_provider_plugins
from pydantic import ValidationError


def test_selected_manifest_is_inert_and_unselected_entries_are_not_loaded(monkeypatch):
    calls = []
    definition = replace(MEM0_OSS, type="custom_memory")
    manifest = ProviderManifest(api_version=1, memory=(definition,))

    def entries(*, group):
        assert group == "a13n_harness.providers.plugins"
        return [
            SimpleNamespace(name="unused", load=lambda: pytest.fail("unselected import")),
            SimpleNamespace(
                name="custom", dist=None, value="fixture:manifest", load=lambda: calls.append("import") or manifest
            ),
        ]

    monkeypatch.setattr("a13n_harness.providers.plugins.importlib.metadata.entry_points", entries)
    assert load_provider_plugins(()) == ()
    assert calls == []
    selected = load_provider_plugins(("custom",))
    catalog = MemoryProviderCatalog(selected[0].manifest.memory)
    assert catalog["custom_memory"] is definition
    assert calls == ["import"]
    with pytest.raises(TypeError):
        catalog["custom_memory"] = definition
    with pytest.raises(ValueError, match="duplicate"):
        MemoryProviderCatalog((definition, definition))
    with pytest.raises(ValueError, match="duplicate"):
        ProviderManifest(api_version=1, memory=(definition, definition))


def test_catalog_snapshots_selection_and_document_support_is_explicit():
    definition = replace(MEM0_OSS, type="custom_memory", supports_documents=False)
    selected = [definition]
    catalog = MemoryProviderCatalog(selected)
    selected.clear()
    assert catalog[definition.type] is definition
    assert not definition.supports_documents
    assert MEM0_OSS.supports_documents
    with pytest.raises(TypeError, match="boolean"):
        replace(definition, supports_documents="yes")


@pytest.mark.parametrize(
    "url", ["file:///tmp/memory", "https://user:secret@host", "https://host?key=secret", "https://host#fragment"]
)
def test_mem0_configuration_keeps_credentials_out_of_urls(url):
    with pytest.raises(ValidationError):
        Mem0OSSConfiguration(base_url=url)


def test_mem0_configuration_and_credential_are_independent_pure_models():
    config = Mem0OSSConfiguration(base_url="http://localhost:8000/")
    assert config.base_url == "http://localhost:8000"
    with pytest.raises(ValidationError):
        Mem0OSSConfiguration(base_url="http://localhost", api_key="secret")
    credential = Mem0Credential(api_key="secret")
    assert "secret" not in repr(credential)
    assert "secret" not in credential.model_dump_json()
    with pytest.raises(ValidationError):
        Mem0Credential(api_key="   ")
    with pytest.raises(ValidationError):
        config.base_url = "http://other"
