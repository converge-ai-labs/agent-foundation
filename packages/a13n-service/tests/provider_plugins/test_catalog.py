from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
from a13n_harness.providers.plugins import ProviderManifest
from a13n_harness.providers.web import WebProviderDefinition
from a13n_service.provider_plugins import ProviderPluginError, load_provider_catalogs
from pydantic import BaseModel, ConfigDict


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str


async def search(configuration, credentials, request, options, transport):
    raise NotImplementedError


def registration(provider_type: str = "custom_web") -> WebProviderDefinition:
    return WebProviderDefinition(
        type=provider_type,
        display_name="Custom Web",
        configuration_model=Configuration,
        credential_model=Credential,
        setup_url="https://example.com/setup",
        search=search,
    )


@dataclass
class Distribution:
    name: str = "provider-package"
    version: str = "1.2.3"

    @property
    def metadata(self) -> dict[str, str]:
        return {"Name": self.name}


class EntryPoint:
    def __init__(self, name: str, register, *, value: str = "provider.plugin:register") -> None:
        self.name = name
        self.value = value
        self.dist = Distribution()
        self._register = register
        self.loads = 0

    def load(self):
        self.loads += 1
        return self._register


def install(monkeypatch: pytest.MonkeyPatch, *entry_points: EntryPoint) -> None:
    """Service selects installed plugins only through the shared Harness loader."""
    monkeypatch.setattr(
        "a13n_harness.providers.plugins.importlib.metadata.entry_points",
        lambda *, group: entry_points,
    )


def test_only_selected_entry_point_is_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = ProviderManifest(api_version=1, web=(registration(),))

    def broken(_registry) -> None:
        raise AssertionError("unselected entry point was imported")

    chosen = EntryPoint("chosen", selected)
    unselected = EntryPoint("broken", broken)
    install(monkeypatch, chosen, unselected)

    catalogs = load_provider_catalogs(("chosen",))

    assert chosen.loads == 1
    assert unselected.loads == 0
    assert catalogs.plugins[0].distribution_name == "provider-package"
    assert catalogs.web["custom_web"].type == "custom_web"


def test_empty_selection_does_not_enumerate_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "a13n_harness.providers.plugins.importlib.metadata.entry_points",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError(f"metadata scanned: {kwargs}")),
    )

    catalogs = load_provider_catalogs(())

    assert catalogs.plugins == ()


@pytest.mark.parametrize("enabled", [("missing",), ("same", "same")])
def test_missing_and_duplicate_selection_fail(monkeypatch: pytest.MonkeyPatch, enabled: tuple[str, ...]) -> None:
    install(monkeypatch)
    with pytest.raises(ProviderPluginError):
        load_provider_catalogs(enabled)


def test_ambiguous_entry_point_fails_without_import(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_points = (EntryPoint("same", ProviderManifest(api_version=1)),) * 2
    install(monkeypatch, *entry_points)
    with pytest.raises(ProviderPluginError, match="ValueError"):
        load_provider_catalogs(("same",))
    assert entry_points[0].loads == 0


def test_incompatible_export_duplicate_type_and_bad_schema_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, EntryPoint("incompatible", object()))
    with pytest.raises(ProviderPluginError, match="TypeError"):
        load_provider_catalogs(("incompatible",))

    duplicate = ProviderManifest(api_version=1, web=(registration("brave"),))

    install(monkeypatch, EntryPoint("duplicate", duplicate))
    with pytest.raises(ProviderPluginError, match="ValueError"):
        load_provider_catalogs(("duplicate",))

    class BadSchema(BaseModel):
        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            del args, kwargs
            return {"type": "array"}

    with pytest.raises(ValueError, match="invalid schema"):
        WebProviderDefinition(
            type="bad_schema",
            display_name="Bad",
            configuration_model=BadSchema,
            credential_model=Credential,
            setup_url="https://example.com",
            search=search,
        )


def test_schema_exporter_must_be_a_pydantic_model(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSchema:
        @classmethod
        def model_json_schema(cls) -> dict[str, str]:
            return {"type": "object"}

    with pytest.raises(ValueError, match="invalid schema"):
        WebProviderDefinition(
            type="fake_schema",
            display_name="Fake Schema",
            configuration_model=cast(type[BaseModel], FakeSchema),
            credential_model=Credential,
            setup_url="https://example.com",
            search=search,
        )


def test_registration_shape_is_checked_before_type_attribute() -> None:
    class FakeRegistration:
        @property
        def type(self) -> str:
            raise AssertionError("registration attributes must not be read")

    with pytest.raises(TypeError, match="immutable tuple"):
        ProviderManifest(api_version=1, web=(cast(WebProviderDefinition, FakeRegistration()),))


def test_manifest_rejects_an_unsupported_api_version():
    with pytest.raises(ValueError, match="API version"):
        ProviderManifest(api_version=2)


def test_environment_manifest_joins_builtins_and_rejects_builtin_collision(monkeypatch):
    from dataclasses import replace

    from a13n_harness.providers.environment.builtins import DIRECT_LOCAL

    definition = replace(DIRECT_LOCAL, type="custom_workspace", display_name="Custom Workspace")
    chosen = EntryPoint("workspace", ProviderManifest(api_version=1, environment=(definition,)))
    install(monkeypatch, chosen)
    catalogs = load_provider_catalogs(("workspace",))
    assert catalogs.environment[definition.type] is definition
    assert len(catalogs.environment) == 12
    install(monkeypatch, EntryPoint("collision", ProviderManifest(api_version=1, environment=(DIRECT_LOCAL,))))
    with pytest.raises(ProviderPluginError, match="ValueError"):
        load_provider_catalogs(("collision",))


def test_memory_manifest_reuses_shared_definition_and_rejects_builtin_collision(monkeypatch):
    from dataclasses import replace

    from a13n_harness.providers.memory.builtins import MEM0_OSS

    definition = replace(MEM0_OSS, type="custom_memory", display_name="Custom Memory")
    chosen = EntryPoint("memory", ProviderManifest(api_version=1, memory=(definition,)))
    install(monkeypatch, chosen)
    catalog = load_provider_catalogs(("memory",)).memory
    assert catalog[definition.type] is definition
    assert chosen.loads == 1
    install(monkeypatch, EntryPoint("collision", ProviderManifest(api_version=1, memory=(MEM0_OSS,))))
    with pytest.raises(ProviderPluginError, match="ValueError"):
        load_provider_catalogs(("collision",))
