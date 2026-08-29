from __future__ import annotations

from dataclasses import dataclass

import pytest
from a13n_service.connectors import (
    ConnectorProvider,
    ConnectorProviderCapabilities,
    ConnectorProviderError,
    ConnectorProviderMetadata,
    ConnectorProviderTrust,
    build_connector_provider_catalog,
)


@dataclass(frozen=True)
class _Distribution:
    name: str
    version: str

    @property
    def metadata(self) -> dict[str, str]:
        return {"Name": self.name}


class _EntryPoint:
    def __init__(
        self,
        name: str,
        target: object,
        *,
        distribution: str = "a13n-connector-test",
        version: str = "1.2.3",
    ) -> None:
        self.name = name
        self.value = "test_provider:Provider"
        self.dist = _Distribution(distribution, version)
        self._target = target
        self.loaded = False

    def load(self) -> object:
        self.loaded = True
        if isinstance(self._target, BaseException):
            raise self._target
        return self._target


class _Provider(ConnectorProvider):
    @property
    def metadata(self) -> ConnectorProviderMetadata:
        return ConnectorProviderMetadata(
            display_name="Test",
            description="Test Connector Provider",
            provider_config_schemas={"1": {"type": "object"}},
            capabilities=ConnectorProviderCapabilities(tools=True),
        )

    def validate_config(self, provider_config_version: str, config) -> None:
        if provider_config_version != "1":
            raise ValueError("unsupported config version")

    async def list_tools(self, context, **kwargs):
        return ()

    async def call_tool(self, context, **kwargs):
        raise AssertionError("not used")


class _InvalidMetadataProvider(_Provider):
    @property
    def metadata(self) -> ConnectorProviderMetadata:
        return ConnectorProviderMetadata(
            display_name=" Test ",
            description="invalid surrounding whitespace",
            provider_config_schemas={"1": {"type": "object"}},
            capabilities=ConnectorProviderCapabilities(tools=True),
        )


class _InvalidSchemaProvider(_Provider):
    @property
    def metadata(self) -> ConnectorProviderMetadata:
        return ConnectorProviderMetadata(
            display_name="Invalid schema",
            description="Schema keyword has an invalid type",
            provider_config_schemas={"1": {"type": 42}},
            capabilities=ConnectorProviderCapabilities(tools=True),
        )


class _MissingCapabilityMethodProvider(ConnectorProvider):
    @property
    def metadata(self) -> ConnectorProviderMetadata:
        return ConnectorProviderMetadata(
            display_name="Missing methods",
            description="Claims tools without implementing their methods",
            provider_config_schemas={"1": {"type": "object"}},
            capabilities=ConnectorProviderCapabilities(tools=True),
        )

    def validate_config(self, provider_config_version: str, config) -> None:
        return None


def _trust(
    provider_key: str = "test",
    *,
    distribution: str = "a13n-connector-test",
    version: str = "1.2.3",
) -> ConnectorProviderTrust:
    return ConnectorProviderTrust(
        provider_key=provider_key,
        distribution_name=distribution,
        distribution_version=version,
    )


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, *entries: _EntryPoint) -> None:
    monkeypatch.setattr(
        "a13n_service.connectors.registry.importlib.metadata.entry_points",
        lambda *, group: entries,
    )


def test_empty_selection_does_not_scan_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_scan(*, group: str) -> tuple[()]:
        raise AssertionError(f"unexpected scan of {group}")

    monkeypatch.setattr("a13n_service.connectors.registry.importlib.metadata.entry_points", fail_scan)

    catalog = build_connector_provider_catalog(())

    assert len(catalog) == 0


def test_catalog_loads_only_exact_selected_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = _EntryPoint("test", _Provider, distribution="A13N.Connector_Test")
    unselected = _EntryPoint("other", RuntimeError("must not import"))
    _patch_entry_points(monkeypatch, unselected, selected)

    catalog = build_connector_provider_catalog((_trust(distribution="a13n-connector-test"),))

    assert catalog.require("test").metadata.display_name == "Test"
    assert selected.loaded is True
    assert unselected.loaded is False
    assert catalog.registration("test").distribution_version == "1.2.3"


@pytest.mark.parametrize(
    ("trust", "code"),
    [
        (_trust(distribution="another-package"), "provider_not_trusted"),
        (_trust(version="2.0.0"), "provider_not_trusted"),
    ],
)
def test_artifact_mismatch_fails_before_import(
    monkeypatch: pytest.MonkeyPatch,
    trust: ConnectorProviderTrust,
    code: str,
) -> None:
    entry = _EntryPoint("test", _Provider)
    _patch_entry_points(monkeypatch, entry)

    with pytest.raises(ConnectorProviderError) as raised:
        build_connector_provider_catalog((trust,))

    assert raised.value.code == code
    assert entry.loaded is False


def test_missing_selected_provider_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch)

    with pytest.raises(ConnectorProviderError) as raised:
        build_connector_provider_catalog((_trust(),))

    assert raised.value.code == "provider_unavailable"


def test_duplicate_entry_point_fails_without_import(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _EntryPoint("test", _Provider)
    second = _EntryPoint("test", _Provider, distribution="other")
    _patch_entry_points(monkeypatch, first, second)

    with pytest.raises(ConnectorProviderError) as raised:
        build_connector_provider_catalog((_trust(),))

    assert raised.value.code == "provider_duplicate"
    assert first.loaded is False
    assert second.loaded is False


def test_invalid_target_and_metadata_fail_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _EntryPoint("test", object)
    _patch_entry_points(monkeypatch, entry)

    with pytest.raises(ConnectorProviderError) as invalid_target:
        build_connector_provider_catalog((_trust(),))

    assert invalid_target.value.code == "provider_target_invalid"

    entry = _EntryPoint("test", _InvalidMetadataProvider)
    _patch_entry_points(monkeypatch, entry)

    with pytest.raises(ConnectorProviderError) as invalid_metadata:
        build_connector_provider_catalog((_trust(),))

    assert invalid_metadata.value.code == "provider_metadata_invalid"

    entry = _EntryPoint("test", _InvalidSchemaProvider)
    _patch_entry_points(monkeypatch, entry)

    with pytest.raises(ConnectorProviderError) as invalid_schema:
        build_connector_provider_catalog((_trust(),))

    assert invalid_schema.value.code == "provider_metadata_invalid"

    entry = _EntryPoint("test", _MissingCapabilityMethodProvider)
    _patch_entry_points(monkeypatch, entry)

    with pytest.raises(ConnectorProviderError) as missing_methods:
        build_connector_provider_catalog((_trust(),))

    assert missing_methods.value.code == "provider_metadata_invalid"


def test_catalog_requires_selected_provider() -> None:
    catalog = build_connector_provider_catalog(())

    with pytest.raises(ConnectorProviderError) as raised:
        catalog.require("missing")

    assert raised.value.code == "provider_unavailable"
