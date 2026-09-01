from __future__ import annotations

from types import SimpleNamespace

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironmentProvider,
    EnvironmentProvider,
    EnvironmentProviderCatalog,
    EnvironmentProviderError,
    build_environment_provider_catalog,
)
from pydantic import BaseModel, ConfigDict, JsonValue


class _Configuration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str


class _Provider(EnvironmentProvider):
    @property
    def key(self) -> str:
        return "test.provider"

    @property
    def configuration_versions(self) -> frozenset[str]:
        return frozenset({"1"})

    def validate_configuration(self, *, schema_version: str, value: JsonValue) -> BaseModel:
        if schema_version != "1":
            raise ValueError("unsupported")
        return _Configuration.model_validate(value)

    def create_environment(self, *, configuration: BaseModel, state, runtime=None):
        del configuration, state, runtime
        raise NotImplementedError


class _FakeEntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target
        self.load_count = 0
        self.dist = SimpleNamespace(name="test-provider", version="1.2.3")

    def load(self) -> object:
        self.load_count += 1
        return self._target


def test_builtin_catalog_resolves_direct_provider_without_scanning_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "a13n_environment_provider.catalog.entry_points",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan metadata")),
    )

    catalog = build_environment_provider_catalog(builtin_keys=("a13n.direct-local",))

    assert catalog.keys == ("a13n.direct-local",)
    assert isinstance(catalog.resolve("a13n.direct-local"), DirectLocalEnvironmentProvider)


def test_extension_catalog_loads_only_explicitly_enabled_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _FakeEntryPoint("test.provider", _Provider)
    ignored = _FakeEntryPoint("ignored.provider", RuntimeError)
    monkeypatch.setattr(
        "a13n_environment_provider.catalog.entry_points",
        lambda **_kwargs: (ignored, selected),
    )

    catalog = build_environment_provider_catalog(extension_keys=("test.provider",))

    provider = catalog.resolve("test.provider")
    assert isinstance(provider, _Provider)
    assert selected.load_count == 1
    assert ignored.load_count == 0
    assert provider.validate_configuration(schema_version="1", value={"name": "sandbox"}) == _Configuration(
        name="sandbox"
    )


def test_catalog_rejects_duplicate_and_unknown_keys() -> None:
    with pytest.raises(EnvironmentProviderError) as duplicate:
        EnvironmentProviderCatalog((_Provider(), _Provider()))
    assert duplicate.value.code == "provider_catalog_invalid"

    catalog = EnvironmentProviderCatalog()
    with pytest.raises(EnvironmentProviderError) as missing:
        catalog.resolve("missing.provider")
    assert missing.value.code == "provider_catalog_invalid"


def test_catalog_rejects_missing_enabled_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("a13n_environment_provider.catalog.entry_points", lambda **_kwargs: ())

    with pytest.raises(EnvironmentProviderError) as captured:
        build_environment_provider_catalog(extension_keys=("test.provider",))

    assert captured.value.code == "provider_catalog_invalid"


def test_catalog_rejects_unknown_builtin() -> None:
    with pytest.raises(EnvironmentProviderError) as captured:
        build_environment_provider_catalog(builtin_keys=("test.provider",))
    assert captured.value.code == "provider_catalog_invalid"
