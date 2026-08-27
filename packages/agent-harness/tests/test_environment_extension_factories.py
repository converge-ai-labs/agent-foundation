from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from a13n_harness import (
    EnvironmentError,
    EnvironmentRunExtensionFactory,
    EnvironmentRunExtensionFactoryContext,
    build_environment_run_extension_factory_catalog,
    discover_environment_run_extension_factory_references,
)


class _Extension:
    def __init__(self, extension_id: str, configuration: dict[str, Any]) -> None:
        self._extension_id = extension_id
        self.configuration = configuration

    @property
    def extension_id(self) -> str:
        return self._extension_id

    @asynccontextmanager
    async def bind(self, *, context) -> AsyncGenerator[None]:
        del context
        yield


class _Factory(EnvironmentRunExtensionFactory):
    calls: ClassVar[list[EnvironmentRunExtensionFactoryContext]] = []

    @classmethod
    def extension_key(cls) -> str:
        return "test.extension"

    def create_extension(self, context: EnvironmentRunExtensionFactoryContext):
        self.calls.append(context)
        return _Extension(context.extension_id, dict(context.configuration))


class _OtherFactory(_Factory):
    @classmethod
    def extension_key(cls) -> str:
        return "test.other"


class _MismatchedKeyFactory(_Factory):
    @classmethod
    def extension_key(cls) -> str:
        return "wrong.key"


class _MismatchedIdFactory(_Factory):
    def create_extension(self, context: EnvironmentRunExtensionFactoryContext):
        return _Extension("wrong-id", dict(context.configuration))


class _ThrowingIdExtension(_Extension):
    @property
    def extension_id(self) -> str:
        raise RuntimeError("secret extension ID")


class _ThrowingIdFactory(_Factory):
    def create_extension(self, context: EnvironmentRunExtensionFactoryContext):
        return _ThrowingIdExtension(context.extension_id, {})


class _InvalidResultFactory(_Factory):
    def create_extension(self, context: EnvironmentRunExtensionFactoryContext):
        del context
        return object()


class _FailingFactory(_Factory):
    def create_extension(self, context: EnvironmentRunExtensionFactoryContext):
        del context
        raise RuntimeError("secret configuration detail")


class _FakeEntryPoint:
    def __init__(
        self,
        name: str,
        target: object,
        *,
        value: str | None = None,
        distribution: str = "test-extension-plugin",
        version: str = "1.2.3",
    ) -> None:
        self.name = name
        self.value = value or f"test_extension:{getattr(target, '__name__', 'target')}"
        self.dist = SimpleNamespace(metadata={"Name": distribution}, version=version)
        self._target = target
        self.load_count = 0

    def load(self) -> object:
        self.load_count += 1
        if isinstance(self._target, BaseException):
            raise self._target
        return self._target


def _context(
    *,
    key: str = "test.extension",
    extension_id: str = "extension-1",
    configuration: dict[str, Any] | None = None,
) -> EnvironmentRunExtensionFactoryContext:
    return EnvironmentRunExtensionFactoryContext(
        extension_key=key,
        extension_id=extension_id,
        configuration=configuration or {},
    )


def test_discovery_reads_metadata_without_importing_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _FakeEntryPoint("test.extension", _Factory)
    second = _FakeEntryPoint("test.other", _OtherFactory, distribution="other-plugin", version="2.0")
    monkeypatch.setattr(
        "a13n_harness.environment.extension_factories._entry_points",
        lambda: (second, first),
    )

    references = discover_environment_run_extension_factory_references()

    assert [reference.extension_key for reference in references] == ["test.extension", "test.other"]
    assert references[0].distribution_name == "test-extension-plugin"
    assert references[0].distribution_version == "1.2.3"
    assert first.load_count == second.load_count == 0


def test_empty_selection_does_not_scan_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "a13n_harness.environment.extension_factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("empty selection must not scan metadata")),
    )

    assert len(build_environment_run_extension_factory_catalog()) == 0


def test_catalog_loads_only_selected_target_and_records_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = _FakeEntryPoint("test.extension", _Factory)
    unselected = _FakeEntryPoint("test.other", RuntimeError("must not load"))
    monkeypatch.setattr(
        "a13n_harness.environment.extension_factories._entry_points",
        lambda: (unselected, selected),
    )

    catalog = build_environment_run_extension_factory_catalog(extension_keys=("test.extension",))

    assert list(catalog) == ["test.extension"]
    assert isinstance(catalog.require("test.extension"), _Factory)
    assert selected.load_count == 1
    assert unselected.load_count == 0
    registration = catalog.registrations[0]
    assert registration.extension_key == "test.extension"
    assert registration.distribution_name == "test-extension-plugin"
    assert registration.import_target == selected.value


def test_explicit_factory_needs_no_metadata_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "a13n_harness.environment.extension_factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("explicit mode must not scan metadata")),
    )

    catalog = build_environment_run_extension_factory_catalog(explicit_factories=(_Factory(),))

    assert isinstance(catalog["test.extension"], _Factory)
    assert catalog.registrations[0].import_target is None


@pytest.mark.parametrize(
    ("selected", "entries", "code"),
    [
        (("missing.extension",), (), "environment_extension_factory_missing"),
        (
            ("test.extension",),
            (_FakeEntryPoint("test.extension", _Factory), _FakeEntryPoint("test.extension", _Factory)),
            "environment_extension_factory_duplicate",
        ),
        (("test.extension", "test.extension"), (), "environment_extension_factory_duplicate"),
    ],
)
def test_catalog_rejects_missing_and_duplicate_selection(
    monkeypatch: pytest.MonkeyPatch,
    selected: tuple[str, ...],
    entries: tuple[_FakeEntryPoint, ...],
    code: str,
) -> None:
    monkeypatch.setattr(
        "a13n_harness.environment.extension_factories._entry_points",
        lambda: entries,
    )

    with pytest.raises(EnvironmentError) as exc_info:
        build_environment_run_extension_factory_catalog(extension_keys=selected)

    assert exc_info.value.code == code


def test_catalog_preflights_explicit_collision_before_import(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint("test.extension", _Factory)
    monkeypatch.setattr(
        "a13n_harness.environment.extension_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(EnvironmentError) as exc_info:
        build_environment_run_extension_factory_catalog(
            extension_keys=("test.extension",),
            explicit_factories=(_Factory(),),
        )

    assert exc_info.value.code == "environment_extension_factory_duplicate"
    assert entry_point.load_count == 0


@pytest.mark.parametrize("target", [object(), object])
def test_catalog_rejects_invalid_entry_point_target(monkeypatch: pytest.MonkeyPatch, target: object) -> None:
    entry_point = _FakeEntryPoint("test.extension", target)
    monkeypatch.setattr(
        "a13n_harness.environment.extension_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(EnvironmentError) as exc_info:
        build_environment_run_extension_factory_catalog(extension_keys=("test.extension",))

    assert exc_info.value.code == "environment_extension_factory_target_invalid"


def test_catalog_rejects_mismatched_extension_key(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint("test.extension", _MismatchedKeyFactory)
    monkeypatch.setattr(
        "a13n_harness.environment.extension_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(EnvironmentError) as exc_info:
        build_environment_run_extension_factory_catalog(extension_keys=("test.extension",))

    assert exc_info.value.code == "environment_extension_factory_key_invalid"


def test_context_detaches_configuration_and_catalog_validates_instance_id() -> None:
    _Factory.calls.clear()
    configuration = {"nested": {"values": [1]}}
    context = _context(configuration=configuration)
    configuration["nested"]["values"].append(2)
    catalog = build_environment_run_extension_factory_catalog(explicit_factories=(_Factory(),))

    extension = catalog.create_extension(context)

    assert extension.extension_id == "extension-1"
    assert isinstance(extension, _Extension)
    assert extension.configuration == {"nested": {"values": [1]}}
    assert _Factory.calls[0] is not context


@pytest.mark.parametrize(
    "configuration",
    [
        {"value": object()},
        {"value": float("nan")},
        {"value": float("inf")},
    ],
)
def test_context_rejects_non_json_configuration(configuration: dict[str, Any]) -> None:
    with pytest.raises(EnvironmentError) as exc_info:
        _context(configuration=configuration)

    assert exc_info.value.code == "environment_extension_factory_context_invalid"


@pytest.mark.parametrize(
    "factory",
    [_MismatchedIdFactory(), _ThrowingIdFactory(), _InvalidResultFactory()],
)
def test_catalog_rejects_invalid_factory_result(factory: EnvironmentRunExtensionFactory) -> None:
    catalog = build_environment_run_extension_factory_catalog(explicit_factories=(factory,))

    with pytest.raises(EnvironmentError) as exc_info:
        catalog.create_extension(_context())

    assert exc_info.value.code == "environment_extension_factory_result_invalid"


def test_catalog_sanitizes_factory_failure() -> None:
    catalog = build_environment_run_extension_factory_catalog(explicit_factories=(_FailingFactory(),))

    with pytest.raises(EnvironmentError) as exc_info:
        catalog.create_extension(_context(configuration={"token": "do-not-render"}))

    assert exc_info.value.code == "environment_extension_factory_failed"
    assert "secret" not in str(exc_info.value)
    assert "token" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
