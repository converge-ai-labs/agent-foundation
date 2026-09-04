from __future__ import annotations

import traceback
from collections.abc import Mapping
from types import SimpleNamespace

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironmentProvider,
    EnvironmentProvider,
    EnvironmentProviderCatalog,
    EnvironmentProviderError,
    EnvironmentProviderRegistration,
    build_environment_provider_catalog,
    discover_environment_provider_references,
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

    def describe_configuration(self, configuration):
        raise NotImplementedError

    def create_environment(self, *, configuration: BaseModel, environment_id: str, state, runtime=None):
        del configuration, state, runtime
        raise NotImplementedError


class _OtherProvider(_Provider):
    @property
    def key(self) -> str:
        return "test.other"


class _MismatchedProvider(_Provider):
    @property
    def key(self) -> str:
        return "test.mismatch"


class _InvalidKeyProvider(_Provider):
    @property
    def key(self) -> str:
        return "INVALID"


class _FailingProvider(_Provider):
    def __init__(self) -> None:
        raise RuntimeError("/private/provider/path: secret")


class _AbstractProvider(EnvironmentProvider):
    pass


class _FakeEntryPoint:
    def __init__(
        self,
        name: str,
        target: object,
        *,
        distribution: str = "test-provider",
        version: str = "1.2.3",
    ) -> None:
        self.name = name
        self.value = f"test_environment_provider:{getattr(target, '__name__', 'provider')}"
        self._target = target
        self.load_count = 0
        self.dist = SimpleNamespace(
            metadata={"Name": distribution},
            version=version,
        )

    def load(self) -> object:
        self.load_count += 1
        if isinstance(self._target, BaseException):
            raise self._target
        return self._target


def test_empty_catalog_selection_does_not_scan_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "a13n_environment_provider.catalog._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("empty selection must not scan metadata")),
    )

    catalog = build_environment_provider_catalog()

    assert isinstance(catalog, Mapping)
    assert len(catalog) == 0
    assert catalog.registrations == ()


def test_discovery_reads_sorted_metadata_without_loading_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _FakeEntryPoint("test.provider", _Provider)
    second = _FakeEntryPoint(
        "test.other",
        _OtherProvider,
        distribution="other-provider",
        version="2.0",
    )
    monkeypatch.setattr(
        "a13n_environment_provider.catalog._entry_points",
        lambda: (first, second),
    )

    references = discover_environment_provider_references()

    assert [reference.provider_key for reference in references] == ["test.other", "test.provider"]
    assert references[0].import_target == second.value
    assert references[0].distribution_name == "other-provider"
    assert references[0].distribution_version == "2.0"
    assert first.load_count == second.load_count == 0


def test_discovery_sanitizes_metadata_enumeration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_scan(*args: object, **kwargs: object) -> tuple[()]:
        del args, kwargs
        raise OSError("/private/install/path: secret")

    monkeypatch.setattr("a13n_environment_provider.catalog.importlib.metadata.entry_points", fail_scan)

    with pytest.raises(EnvironmentProviderError) as exc_info:
        discover_environment_provider_references()

    assert exc_info.value.code == "provider_catalog_load_failed"
    assert exc_info.value.context.provider_key is None
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert "secret" not in rendered
    assert "/private/install/path" not in rendered


def test_builtin_catalog_resolves_provider_without_scanning_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "a13n_environment_provider.catalog._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("built-ins must not scan metadata")),
    )

    catalog = build_environment_provider_catalog(builtin_keys=("a13n.direct-local",))

    assert tuple(catalog) == ("a13n.direct-local",)
    assert isinstance(catalog.require("a13n.direct-local"), DirectLocalEnvironmentProvider)
    registration = catalog.registrations[0]
    assert registration.provider_key == "a13n.direct-local"
    assert registration.builtin is True
    assert registration.import_target is None
    assert registration.distribution_name is None


def test_extension_catalog_loads_only_selected_provider_and_records_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _FakeEntryPoint("test.provider", _Provider)
    ignored = _FakeEntryPoint("test.other", RuntimeError("must not load"))
    monkeypatch.setattr(
        "a13n_environment_provider.catalog._entry_points",
        lambda: (ignored, selected),
    )

    catalog = build_environment_provider_catalog(extension_keys=("test.provider",))

    provider = catalog.require("test.provider")
    assert isinstance(provider, _Provider)
    assert selected.load_count == 1
    assert ignored.load_count == 0
    assert provider.validate_configuration(schema_version="1", value={"name": "sandbox"}) == _Configuration(
        name="sandbox"
    )
    registration = catalog.registrations[0]
    assert registration.provider_key == "test.provider"
    assert registration.class_module == _Provider.__module__
    assert registration.class_qualname == _Provider.__qualname__
    assert registration.import_target == selected.value
    assert registration.distribution_name == "test-provider"
    assert registration.distribution_version == "1.2.3"
    assert registration.builtin is False


def test_explicit_provider_needs_no_metadata_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "a13n_environment_provider.catalog._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("explicit Providers must not scan metadata")),
    )
    provider = _Provider()

    catalog = build_environment_provider_catalog(explicit_providers=(provider,))

    assert catalog["test.provider"] is provider
    assert tuple(catalog.keys()) == ("test.provider",)
    assert not hasattr(catalog, "register")
    assert not hasattr(catalog, "load_entry_points")
    registration = catalog.registrations[0]
    assert registration.builtin is False
    assert registration.import_target is None
    assert registration.distribution_name is None
    assert registration.distribution_version is None


def test_catalog_registration_order_is_builtin_extension_then_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _FakeEntryPoint("test.other", _OtherProvider)
    monkeypatch.setattr("a13n_environment_provider.catalog._entry_points", lambda: (selected,))

    catalog = build_environment_provider_catalog(
        builtin_keys=("a13n.direct-local",),
        extension_keys=("test.other",),
        explicit_providers=(_Provider(),),
    )

    assert tuple(catalog) == ("a13n.direct-local", "test.other", "test.provider")
    assert [registration.provider_key for registration in catalog.registrations] == [
        "a13n.direct-local",
        "test.other",
        "test.provider",
    ]
    assert [registration.builtin for registration in catalog.registrations] == [True, False, False]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"builtin_keys": ("INVALID",)}, "provider_catalog_key_invalid"),
        ({"extension_keys": ("test.provider", "test.provider")}, "provider_catalog_duplicate"),
        ({"builtin_keys": ("test.provider",)}, "provider_catalog_missing"),
        ({"explicit_providers": (_Provider(), _Provider())}, "provider_catalog_duplicate"),
        ({"explicit_providers": (object(),)}, "provider_catalog_target_invalid"),
        ({"explicit_providers": (_InvalidKeyProvider(),)}, "provider_catalog_key_invalid"),
    ],
)
def test_catalog_rejects_invalid_local_selection_without_scanning_metadata(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
    code: str,
) -> None:
    monkeypatch.setattr(
        "a13n_environment_provider.catalog._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("invalid local selection must fail first")),
    )

    with pytest.raises(EnvironmentProviderError) as exc_info:
        build_environment_provider_catalog(**kwargs)

    assert exc_info.value.code == code


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "builtin_keys": ("a13n.direct-local",),
            "extension_keys": ("a13n.direct-local",),
        },
        {
            "extension_keys": ("test.provider",),
            "explicit_providers": (_Provider(),),
        },
    ],
)
def test_catalog_preflights_source_collisions_before_metadata_scan(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
) -> None:
    monkeypatch.setattr(
        "a13n_environment_provider.catalog._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("collisions must fail before metadata scan")),
    )

    with pytest.raises(EnvironmentProviderError) as exc_info:
        build_environment_provider_catalog(**kwargs)

    assert exc_info.value.code == "provider_catalog_duplicate"


def test_catalog_preflights_missing_entry_point_before_loading_any_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _FakeEntryPoint("test.provider", _Provider)
    monkeypatch.setattr("a13n_environment_provider.catalog._entry_points", lambda: (selected,))

    with pytest.raises(EnvironmentProviderError) as exc_info:
        build_environment_provider_catalog(
            extension_keys=("test.provider", "test.missing"),
        )

    assert exc_info.value.code == "provider_catalog_missing"
    assert exc_info.value.context.provider_key == "test.missing"
    assert selected.load_count == 0


def test_catalog_preflights_duplicate_entry_points_before_loading_any_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _FakeEntryPoint("test.provider", _Provider)
    second = _FakeEntryPoint("test.provider", _Provider, distribution="duplicate-provider")
    monkeypatch.setattr("a13n_environment_provider.catalog._entry_points", lambda: (first, second))

    with pytest.raises(EnvironmentProviderError) as exc_info:
        build_environment_provider_catalog(extension_keys=("test.provider",))

    assert exc_info.value.code == "provider_catalog_duplicate"
    assert first.load_count == second.load_count == 0


@pytest.mark.parametrize(
    ("target", "code"),
    [
        (_Provider(), "provider_catalog_target_invalid"),
        (object, "provider_catalog_target_invalid"),
        (_AbstractProvider, "provider_catalog_target_invalid"),
        (_MismatchedProvider, "provider_catalog_key_invalid"),
        (_FailingProvider, "provider_catalog_load_failed"),
    ],
)
def test_catalog_rejects_invalid_entry_point_contract(
    monkeypatch: pytest.MonkeyPatch,
    target: object,
    code: str,
) -> None:
    selected = _FakeEntryPoint("test.provider", target)
    monkeypatch.setattr("a13n_environment_provider.catalog._entry_points", lambda: (selected,))

    with pytest.raises(EnvironmentProviderError) as exc_info:
        build_environment_provider_catalog(extension_keys=("test.provider",))

    assert exc_info.value.code == code
    assert exc_info.value.context.provider_key == "test.provider"
    assert selected.load_count == 1


def test_catalog_sanitizes_selected_target_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = _FakeEntryPoint(
        "test.provider",
        RuntimeError("/private/provider/path: secret"),
    )
    monkeypatch.setattr("a13n_environment_provider.catalog._entry_points", lambda: (selected,))

    with pytest.raises(EnvironmentProviderError) as exc_info:
        build_environment_provider_catalog(extension_keys=("test.provider",))

    assert exc_info.value.code == "provider_catalog_load_failed"
    assert exc_info.value.context.distribution_name == "test-provider"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert "secret" not in rendered
    assert "/private/provider/path" not in rendered


def test_public_catalog_constructor_does_not_accept_caller_authored_provenance() -> None:
    provider = _Provider()
    forged = EnvironmentProviderRegistration(
        provider_key=provider.key,
        class_module="trusted.provider",
        class_qualname="TrustedProvider",
        import_target="trusted.provider:TrustedProvider",
        distribution_name="trusted-distribution",
        distribution_version="9.9.9",
        builtin=True,
    )

    with pytest.raises(TypeError):
        EnvironmentProviderCatalog(((forged, provider),))  # type: ignore[call-arg]


def test_catalog_require_validates_key_and_reports_missing_selection() -> None:
    catalog = build_environment_provider_catalog(explicit_providers=(_Provider(),))

    with pytest.raises(KeyError):
        _ = catalog["test.missing"]
    with pytest.raises(EnvironmentProviderError) as missing:
        catalog.require("test.missing")
    with pytest.raises(EnvironmentProviderError) as invalid:
        catalog.require("INVALID")

    assert missing.value.code == "provider_catalog_missing"
    assert missing.value.context.provider_key == "test.missing"
    assert invalid.value.code == "provider_catalog_key_invalid"
