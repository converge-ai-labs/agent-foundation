from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
from a13n_service.provider_plugins import (
    ProviderPluginError,
    ProviderPluginRegistry,
    WebProviderRegistration,
    load_provider_catalogs,
    provider_plugin,
)
from a13n_service.web.registry import WebProviderRegistry
from anyio import current_time, fail_after, sleep_forever
from pydantic import BaseModel, ConfigDict


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str


class Runtime:
    async def search(self, **kwargs):
        raise NotImplementedError

    async def scrape(self, **kwargs):
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


def registration(provider_type: str = "custom_web") -> WebProviderRegistration:
    return WebProviderRegistration(
        type=provider_type,
        display_name="Custom Web",
        configuration_model=Configuration,
        credential_model=Credential,
        setup_url="https://example.com/setup",
        factory=Runtime,
        supports_search=True,
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


def compatible(register):
    return provider_plugin(api_version=1)(register)


def install(monkeypatch: pytest.MonkeyPatch, *entry_points: EntryPoint) -> None:
    monkeypatch.setattr(
        "a13n_service.provider_plugins.catalog.importlib.metadata.entry_points",
        lambda *, group: entry_points,
    )


def test_only_selected_entry_point_is_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    @compatible
    def selected(registry) -> None:
        registry.web.register(registration())

    def broken(_registry) -> None:
        raise AssertionError("unselected entry point was imported")

    chosen = EntryPoint("chosen", selected)
    unselected = EntryPoint("broken", broken)
    install(monkeypatch, chosen, unselected)

    catalogs = load_provider_catalogs(("chosen",))

    assert chosen.loads == 1
    assert unselected.loads == 0
    assert catalogs.plugins[0].distribution_name == "provider-package"
    assert any(item.type == "custom_web" for item in catalogs.web)


def test_empty_selection_does_not_enumerate_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "a13n_service.provider_plugins.catalog.importlib.metadata.entry_points",
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
    entry_points = (EntryPoint("same", compatible(lambda registry: None)),) * 2
    install(monkeypatch, *entry_points)
    with pytest.raises(ProviderPluginError, match="ambiguous"):
        load_provider_catalogs(("same",))
    assert entry_points[0].loads == 0


def test_incompatible_api_duplicate_type_and_bad_schema_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def incompatible(_registry) -> None:
        pass

    install(monkeypatch, EntryPoint("incompatible", incompatible))
    with pytest.raises(ProviderPluginError, match="TypeError"):
        load_provider_catalogs(("incompatible",))

    @compatible
    def duplicate(registry) -> None:
        registry.web.register(registration("brave"))

    install(monkeypatch, EntryPoint("duplicate", duplicate))
    with pytest.raises(ProviderPluginError, match="ValueError"):
        load_provider_catalogs(("duplicate",))

    class BadSchema(BaseModel):
        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            del args, kwargs
            return {"type": "array"}

    @compatible
    def bad_schema(registry) -> None:
        registry.web.register(
            WebProviderRegistration(
                type="bad_schema",
                display_name="Bad",
                configuration_model=BadSchema,
                credential_model=Credential,
                setup_url="https://example.com",
                factory=Runtime,
                supports_search=True,
            )
        )

    install(monkeypatch, EntryPoint("bad_schema", bad_schema))
    with pytest.raises(ProviderPluginError, match="invalid schema"):
        load_provider_catalogs(("bad_schema",))


def test_declared_old_api_is_rejected_before_callback_on_newer_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    @provider_plugin(api_version=1)
    def old_plugin(_registry) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("a13n_service.provider_plugins.api.PROVIDER_EXTENSION_API_VERSION", 2)
    monkeypatch.setattr("a13n_service.provider_plugins.catalog.PROVIDER_EXTENSION_API_VERSION", 2)
    install(monkeypatch, EntryPoint("old", old_plugin))

    with pytest.raises(ProviderPluginError, match="TypeError"):
        load_provider_catalogs(("old",))

    assert not called


def test_schema_exporter_must_be_a_pydantic_model(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSchema:
        @classmethod
        def model_json_schema(cls) -> dict[str, str]:
            return {"type": "object"}

    @compatible
    def fake_schema(registry) -> None:
        registry.web.register(
            WebProviderRegistration(
                type="fake_schema",
                display_name="Fake Schema",
                configuration_model=cast(type[BaseModel], FakeSchema),
                credential_model=Credential,
                setup_url="https://example.com",
                factory=Runtime,
                supports_search=True,
            )
        )

    install(monkeypatch, EntryPoint("fake_schema", fake_schema))
    with pytest.raises(ProviderPluginError, match="invalid schema"):
        load_provider_catalogs(("fake_schema",))


def test_registration_shape_is_checked_before_type_attribute() -> None:
    class FakeRegistration:
        @property
        def type(self) -> str:
            raise AssertionError("registration attributes must not be read")

    plugin_registry = ProviderPluginRegistry(api_version=1)

    with pytest.raises(TypeError, match="invalid type"):
        plugin_registry.web.register(cast(WebProviderRegistration, FakeRegistration()))


@pytest.mark.anyio
async def test_web_runtime_hanging_cleanup_is_bounded_and_preserves_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_started = 0

    class HangingRuntime(Runtime):
        async def aclose(self) -> None:
            nonlocal cleanup_started
            cleanup_started += 1
            await sleep_forever()

    monkeypatch.setattr("a13n_service.web.registry._RUNTIME_CLEANUP_TIMEOUT_SECONDS", 0.01)
    runtimes = [HangingRuntime(), HangingRuntime(), HangingRuntime()]
    provider = registration()
    registry = WebProviderRegistry(
        (
            WebProviderRegistration(
                type=provider.type,
                display_name=provider.display_name,
                configuration_model=provider.configuration_model,
                credential_model=provider.credential_model,
                setup_url=provider.setup_url,
                factory=runtimes.pop,
                supports_search=True,
            ),
        )
    )

    started = current_time()
    async with registry.runtime("custom_web"):
        result = "paid operation completed"
    assert result == "paid operation completed"
    assert current_time() - started < 0.5

    original_error = RuntimeError("operation failed")
    with pytest.raises(RuntimeError, match="operation failed") as raised:
        async with registry.runtime("custom_web"):
            raise original_error
    assert raised.value is original_error

    with pytest.raises(TimeoutError):
        with fail_after(0.01):
            async with registry.runtime("custom_web"):
                await sleep_forever()

    assert cleanup_started == 3
    assert runtimes == []
    assert current_time() - started < 0.5


@pytest.mark.anyio
async def test_web_runtime_cleanup_log_excludes_external_error_details(caplog: pytest.LogCaptureFixture) -> None:
    class FailingRuntime(Runtime):
        async def aclose(self) -> None:
            raise RuntimeError("credential=must-not-appear")

    provider = registration()
    registry = WebProviderRegistry(
        (
            WebProviderRegistration(
                type=provider.type,
                display_name=provider.display_name,
                configuration_model=provider.configuration_model,
                credential_model=provider.credential_model,
                setup_url=provider.setup_url,
                factory=FailingRuntime,
                supports_search=True,
            ),
        )
    )

    with caplog.at_level("WARNING", logger="a13n_service.web.registry"):
        async with registry.runtime("custom_web"):
            result = "operation completed"

    assert result == "operation completed"
    assert "credential=must-not-appear" not in caplog.text
    assert caplog.records[0].cleanup_error_type == "RuntimeError"
