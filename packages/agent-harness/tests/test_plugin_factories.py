from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from a13n_harness import (
    HARNESS_PLUGIN_ENTRY_POINT_GROUP,
    AbstractHarnessPlugin,
    HarnessBuildContext,
    HarnessBuilder,
    HarnessPluginFactory,
    HarnessPluginFactoryContext,
    PluginError,
    build_harness_plugin_factory_catalog,
    discover_harness_plugin_factory_references,
)
from pydantic_ai.agent.spec import AgentSpec


class _HarnessPlugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str, name: str) -> None:
        self._plugin_id = plugin_id
        self.name = name

    @property
    def plugin_id(self) -> str:
        return self._plugin_id


class _Factory(HarnessPluginFactory):
    calls: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    def plugin_key(cls) -> str:
        return "test.plugin"

    def create_plugin(self, context: HarnessPluginFactoryContext):
        detached = dict(context.configuration)
        self.calls.append(detached)
        return _HarnessPlugin(context.plugin_id, str(detached.get("name", "default")))


class _OtherFactory(_Factory):
    @classmethod
    def plugin_key(cls) -> str:
        return "test.other"


class _MismatchedFactory(_Factory):
    @classmethod
    def plugin_key(cls) -> str:
        return "wrong.key"


class _InvalidResultFactory(_Factory):
    def create_plugin(self, context: HarnessPluginFactoryContext):
        del context
        return object()


class _MismatchedResultFactory(_Factory):
    def create_plugin(self, context: HarnessPluginFactoryContext):
        return _HarnessPlugin(f"{context.plugin_id}.wrong", "wrong")


class _FailingFactory(_Factory):
    def create_plugin(self, context: HarnessPluginFactoryContext):
        del context
        raise RuntimeError("secret factory detail")


class _RequiresArgumentFactory(_Factory):
    def __init__(self, required: str) -> None:
        self.required = required


def _build_context(
    configuration: dict[str, Any],
    *,
    plugin_id: str = "test-plugin-1",
    extensions: dict[str, Any] | None = None,
) -> HarnessPluginFactoryContext:
    return HarnessPluginFactoryContext(
        plugin_key="test.plugin",
        plugin_id=plugin_id,
        configuration=configuration,
        extensions=extensions or {},
    )


class _FailingDistribution:
    @property
    def metadata(self) -> dict[str, str]:
        raise OSError("/private/install/path/METADATA: secret")

    @property
    def version(self) -> str:
        return "1.2.3"


class _FakeEntryPoint:
    def __init__(
        self,
        name: str,
        target: object,
        *,
        value: str | None = None,
        distribution: str = "test-harness-plugin",
        version: str = "1.2.3",
    ) -> None:
        self.name = name
        self.value = value or f"test_harness:{getattr(target, '__name__', 'target')}"
        self.dist = SimpleNamespace(metadata={"Name": distribution}, version=version)
        self._target = target
        self.load_count = 0

    def load(self) -> object:
        self.load_count += 1
        if isinstance(self._target, BaseException):
            raise self._target
        return self._target


def test_discovery_reads_metadata_without_importing_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _FakeEntryPoint("test.plugin", _Factory)
    second = _FakeEntryPoint("test.other", _OtherFactory, distribution="other-plugin", version="2.0")
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (second, first),
    )

    references = discover_harness_plugin_factory_references()

    assert [reference.plugin_key for reference in references] == ["test.other", "test.plugin"]
    assert references[1].distribution_name == "test-harness-plugin"
    assert references[1].distribution_version == "1.2.3"
    assert first.load_count == second.load_count == 0


def test_new_builder_finds_completed_distribution_added_to_import_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugin_key = "test.runtime-added"
    module_name = "runtime_added_harness_plugin"
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    importlib.invalidate_caches()

    assert plugin_key not in {reference.plugin_key for reference in discover_harness_plugin_factory_references()}

    package = plugin_root / module_name
    package.mkdir()
    (package / "__init__.py").write_text(
        f'''from a13n_harness import AbstractHarnessPlugin, HarnessPluginFactory


class RuntimeAddedPlugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id

    @property
    def plugin_id(self) -> str:
        return self._plugin_id


class RuntimeAddedFactory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls) -> str:
        return "{plugin_key}"

    def create_plugin(self, context):
        return RuntimeAddedPlugin(context.plugin_id)
''',
        encoding="utf-8",
    )
    dist_info = plugin_root / "runtime_added_harness_plugin-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: runtime-added-harness-plugin\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        f"[{HARNESS_PLUGIN_ENTRY_POINT_GROUP}]\n{plugin_key} = {module_name}:RuntimeAddedFactory\n",
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(plugin_root))
    importlib.invalidate_caches()

    references = discover_harness_plugin_factory_references()
    catalog = build_harness_plugin_factory_catalog(plugin_keys=(plugin_key,))
    plugin = catalog.create_plugin(
        HarnessPluginFactoryContext(
            plugin_key=plugin_key,
            plugin_id="runtime-added-1",
            configuration={},
            extensions={},
        )
    )
    context = HarnessBuildContext.from_configuration(
        {
            "schema_version": "1",
            "plugins": [
                {
                    "plugin_id": "runtime-added-2",
                    "plugin_key": plugin_key,
                    "enabled": True,
                    "configuration": {},
                }
            ],
        }
    )
    executable = HarnessBuilder(build_context=context).build_code(
        AgentSpec(model="test"),
        output_type=str,
    )

    assert plugin_key in {reference.plugin_key for reference in references}
    assert plugin.plugin_id == "runtime-added-1"
    assert type(plugin).__module__ == module_name
    assert [item.plugin_id for item in executable._plugins] == ["runtime-added-2"]
    sys.modules.pop(module_name, None)


def test_discovery_sanitizes_metadata_enumeration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_scan(*args: object, **kwargs: object) -> tuple[()]:
        del args, kwargs
        raise OSError("/private/install/path: secret")

    monkeypatch.setattr("a13n_harness.plugin_factories.importlib.metadata.entry_points", fail_scan)

    with pytest.raises(PluginError) as exc_info:
        discover_harness_plugin_factory_references()

    assert exc_info.value.code == "plugin_factory_load_failed"
    assert exc_info.value.details == {}
    assert "secret" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    assert "secret" not in "".join(traceback.format_exception(exc_info.value))
    assert "/private/install/path" not in "".join(traceback.format_exception(exc_info.value))


def test_empty_selection_does_not_scan_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_scan() -> tuple[()]:
        raise AssertionError("empty selection must not scan installed metadata")

    monkeypatch.setattr("a13n_harness.plugin_factories._entry_points", reject_scan)

    assert len(build_harness_plugin_factory_catalog()) == 0


def test_catalog_loads_only_selected_target_and_records_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = _FakeEntryPoint("test.plugin", _Factory)
    unselected = _FakeEntryPoint("test.other", RuntimeError("must not load"))
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (unselected, selected),
    )

    catalog = build_harness_plugin_factory_catalog(plugin_keys=("test.plugin",))

    assert list(catalog) == ["test.plugin"]
    assert isinstance(catalog.require("test.plugin"), _Factory)
    assert selected.load_count == 1
    assert unselected.load_count == 0
    registration = catalog.registrations[0]
    assert registration.plugin_key == "test.plugin"
    assert registration.distribution_name == "test-harness-plugin"
    assert registration.distribution_version == "1.2.3"
    assert registration.import_target == selected.value


def test_explicit_factories_need_no_metadata_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    catalog = build_harness_plugin_factory_catalog(explicit_factories=(_Factory(),))

    assert isinstance(catalog["test.plugin"], _Factory)
    assert catalog.registrations[0].import_target is None


@pytest.mark.parametrize(
    ("selected", "entries", "code"),
    [
        (("missing.plugin",), (), "plugin_factory_missing"),
        (
            ("test.plugin",),
            (_FakeEntryPoint("test.plugin", _Factory), _FakeEntryPoint("test.plugin", _Factory)),
            "plugin_factory_duplicate",
        ),
        (("test.plugin", "test.plugin"), (), "plugin_factory_duplicate"),
    ],
)
def test_catalog_rejects_missing_and_duplicate_selection(
    monkeypatch: pytest.MonkeyPatch,
    selected: tuple[str, ...],
    entries: tuple[_FakeEntryPoint, ...],
    code: str,
) -> None:
    monkeypatch.setattr("a13n_harness.plugin_factories._entry_points", lambda: entries)

    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(plugin_keys=selected)

    assert exc_info.value.code == code


def test_catalog_preflights_missing_selection_before_import(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint("test.plugin", _Factory)
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(
            plugin_keys=("test.plugin", "missing.plugin"),
        )

    assert exc_info.value.code == "plugin_factory_missing"
    assert entry_point.load_count == 0


def test_catalog_preflights_explicit_collision_before_import(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint("test.plugin", _Factory)
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(
            plugin_keys=("test.plugin",),
            explicit_factories=(_Factory(),),
        )

    assert exc_info.value.code == "plugin_factory_duplicate"
    assert entry_point.load_count == 0


@pytest.mark.parametrize("target", [object(), object])
def test_catalog_rejects_invalid_entry_point_target(
    monkeypatch: pytest.MonkeyPatch,
    target: object,
) -> None:
    entry_point = _FakeEntryPoint("test.plugin", target)
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(plugin_keys=("test.plugin",))

    assert exc_info.value.code == "plugin_factory_target_invalid"


def test_catalog_sanitizes_distribution_metadata_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint("test.plugin", _Factory)
    entry_point.dist = _FailingDistribution()
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(plugin_keys=("test.plugin",))

    assert exc_info.value.code == "plugin_factory_load_failed"
    assert exc_info.value.details == {"plugin_key": "test.plugin"}
    assert "secret" not in str(exc_info.value)
    assert "private" not in str(exc_info.value.details)
    assert entry_point.load_count == 0
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_catalog_sanitizes_entry_point_load_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint(
        "test.plugin",
        RuntimeError("secret installation path"),
        value="private.module:factory",
    )
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(plugin_keys=("test.plugin",))

    assert exc_info.value.code == "plugin_factory_load_failed"
    assert "secret" not in str(exc_info.value)
    assert "private.module" not in str(exc_info.value.details)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    formatted = "".join(traceback.format_exception(exc_info.value))
    assert "secret installation path" not in formatted
    assert "private.module" not in formatted


def test_catalog_rejects_factory_that_needs_constructor_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_point = _FakeEntryPoint("test.plugin", _RequiresArgumentFactory)
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(plugin_keys=("test.plugin",))

    assert exc_info.value.code == "plugin_factory_load_failed"


def test_catalog_rejects_mismatched_plugin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint("test.plugin", _MismatchedFactory)
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(plugin_keys=("test.plugin",))

    assert exc_info.value.code == "plugin_factory_key_invalid"


def test_catalog_rejects_invalid_explicit_factory() -> None:
    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(explicit_factories=(object(),))  # type: ignore[arg-type]

    assert exc_info.value.code == "plugin_factory_target_invalid"


@pytest.mark.parametrize("plugin_key", ["", " padded", "padded ", "x" * 201])
def test_catalog_rejects_invalid_factory_key(plugin_key: str) -> None:
    with pytest.raises(PluginError) as exc_info:
        build_harness_plugin_factory_catalog(plugin_keys=(plugin_key,))

    assert exc_info.value.code == "plugin_factory_key_invalid"


def test_catalog_validates_factory_configuration_and_plugin() -> None:
    _Factory.calls.clear()
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(_Factory(),))
    configuration = {"name": "first", "nested": {"values": [1]}}

    context = _build_context(configuration, extensions={"foundation": {"region": "test"}})
    plugin = catalog.create_plugin(context)
    configuration["nested"]["values"].append(2)

    assert isinstance(plugin, _HarnessPlugin)
    assert plugin.plugin_id == "test-plugin-1"
    assert plugin.name == "first"
    assert _Factory.calls == [{"name": "first", "nested": {"values": [1]}}]
    assert context.extensions == {"foundation": {"region": "test"}}


@pytest.mark.parametrize(
    "configuration",
    [
        {"value": object()},
        {"value": float("nan")},
        {"value": float("inf")},
    ],
)
def test_catalog_rejects_non_json_factory_configuration(configuration: dict[str, Any]) -> None:
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(_Factory(),))

    with pytest.raises(PluginError) as exc_info:
        catalog.create_plugin(_build_context(configuration))

    assert exc_info.value.code == "plugin_factory_context_invalid"


def test_catalog_rejects_invalid_factory_result() -> None:
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(_InvalidResultFactory(),))

    with pytest.raises(PluginError) as exc_info:
        catalog.create_plugin(_build_context({}))

    assert exc_info.value.code == "plugin_factory_result_invalid"


def test_catalog_rejects_mismatched_factory_result_id() -> None:
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(_MismatchedResultFactory(),))

    with pytest.raises(PluginError) as exc_info:
        catalog.create_plugin(_build_context({}, plugin_id="expected-1"))

    assert exc_info.value.code == "plugin_factory_result_invalid"
    assert exc_info.value.details == {"plugin_key": "test.plugin", "plugin_id": "expected-1"}


def test_catalog_sanitizes_factory_failure() -> None:
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(_FailingFactory(),))
    configuration = {"token": "do-not-render"}
    context = _build_context(configuration)

    with pytest.raises(PluginError) as exc_info:
        catalog.create_plugin(context)

    assert exc_info.value.code == "plugin_factory_failed"
    assert "secret" not in str(exc_info.value)
    assert "token" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    formatted = "".join(traceback.format_exception(exc_info.value))
    assert "secret factory detail" not in formatted
    assert "do-not-render" not in formatted


def test_catalog_require_rejects_unselected_key() -> None:
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(_Factory(),))

    with pytest.raises(PluginError) as exc_info:
        catalog.require("missing.plugin")

    assert exc_info.value.code == "plugin_factory_missing"
