from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from a13n_harness.memory_plugins import (
    MEMORY_BACKEND_ENTRY_POINT_GROUP,
    Mem0Credential,
    Mem0OSSBackendPlugin,
    Mem0OSSConfiguration,
    MemoryBackendCatalog,
    MemoryBackendPlugin,
    build_memory_backend_catalog,
)
from pydantic import BaseModel, ConfigDict, ValidationError


class _Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    collection: str


class _Credential(BaseModel):
    token: str


class _Plugin(MemoryBackendPlugin[_Configuration, _Credential]):
    key = "test.memory"
    display_name = "Test memory"
    configuration_model = _Configuration
    credential_model = _Credential

    @asynccontextmanager
    async def open(self, configuration, credential):
        raise AssertionError("Catalog and validation must not open a backend")
        yield


def test_catalog_is_explicit_and_does_not_import_unselected_targets(monkeypatch):
    calls = []

    def entry_points(*, group):
        assert group == MEMORY_BACKEND_ENTRY_POINT_GROUP
        return [
            SimpleNamespace(name="unselected.memory", load=lambda: pytest.fail("Unselected target imported")),
            SimpleNamespace(name="test.memory", load=lambda: calls.append("import") or _Plugin),
        ]

    monkeypatch.setattr("a13n_harness.memory_plugins.importlib.metadata.entry_points", entry_points)
    assert not build_memory_backend_catalog()
    assert calls == []
    catalog = build_memory_backend_catalog(builtin_keys=("a13n.mem0-oss",), extension_keys=("test.memory",))
    assert tuple(catalog) == ("a13n.mem0-oss", "test.memory")
    assert calls == ["import"]
    plugin = catalog["test.memory"]
    assert plugin.configuration_model.model_validate({"collection": "facts"}).collection == "facts"
    assert plugin.credential_model.model_validate({"token": "private"}).token == "private"
    with pytest.raises(TypeError):
        catalog["test.memory"] = _Plugin()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"builtin_keys": ("a13n.mem0-oss", "a13n.mem0-oss")},
        {"builtin_keys": ("a13n.mem0-oss",), "explicit_plugins": (Mem0OSSBackendPlugin(),)},
        {"extension_keys": ("test.memory",), "explicit_plugins": (_Plugin(),)},
        {"explicit_plugins": (_Plugin(), _Plugin())},
        {"builtin_keys": ("not.available",)},
        {"builtin_keys": ("invalid key",)},
    ],
)
def test_catalog_rejects_collisions_before_loading_code(monkeypatch, kwargs):
    monkeypatch.setattr(
        "a13n_harness.memory_plugins.importlib.metadata.entry_points",
        lambda **kwargs: pytest.fail("Invalid selection must fail before discovery"),
    )
    with pytest.raises(ValueError):
        build_memory_backend_catalog(**kwargs)


@pytest.mark.parametrize("entries", [(), ("test.memory", "test.memory")])
def test_catalog_rejects_missing_or_ambiguous_installed_keys(monkeypatch, entries):
    monkeypatch.setattr(
        "a13n_harness.memory_plugins.importlib.metadata.entry_points",
        lambda **kwargs: [
            SimpleNamespace(name=key, load=lambda: pytest.fail("Ambiguous target imported")) for key in entries
        ],
    )
    with pytest.raises(ValueError):
        build_memory_backend_catalog(extension_keys=("test.memory",))


def test_direct_catalog_borrows_plugin_objects_and_snapshots_selection():
    plugin = _Plugin()
    selected = [plugin]
    catalog = MemoryBackendCatalog(selected)
    selected.clear()
    assert catalog[plugin.key] is plugin


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


def test_document_capability_is_inert_and_opt_in():
    plugin = _Plugin()
    catalog = MemoryBackendCatalog((plugin, Mem0OSSBackendPlugin()))
    assert catalog[plugin.key].supports_documents is False
    assert catalog["a13n.mem0-oss"].supports_documents is True
    plugin.supports_documents = "yes"
    with pytest.raises(TypeError, match="boolean"):
        MemoryBackendCatalog((plugin,))
