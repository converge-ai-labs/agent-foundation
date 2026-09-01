from __future__ import annotations

import json
import traceback
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AgentDefinition,
    HarnessBuilder,
    PluginError,
    SubagentDefinition,
)
from a13n_harness.plugin_configuration import (
    DEFAULT_HARNESS_PLUGIN_CONFIG_FILE,
    HARNESS_PLUGIN_CONFIG_ENABLED_ENV,
    HARNESS_PLUGIN_CONFIG_FILE_ENV,
    HARNESS_PLUGIN_CONFIG_JSON_ENV,
    HarnessBuildContext,
)
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryContext,
)
from pydantic_ai.agent.spec import AgentSpec


class _ConfiguredPlugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str, marker: str) -> None:
        self._plugin_id = plugin_id
        self.marker = marker

    @property
    def plugin_id(self) -> str:
        return self._plugin_id


class _ConfiguredFactory(HarnessPluginFactory):
    contexts: ClassVar[list[HarnessPluginFactoryContext]] = []
    plugins: ClassVar[list[_ConfiguredPlugin]] = []

    @classmethod
    def plugin_key(cls) -> str:
        return "test.configured"

    def create_plugin(self, context: HarnessPluginFactoryContext) -> AbstractHarnessPlugin:
        plugin = _ConfiguredPlugin(context.plugin_id, str(context.configuration.get("marker", "default")))
        self.contexts.append(context)
        self.plugins.append(plugin)
        return plugin


class _FakeEntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self.value = f"test_configuration:{getattr(target, '__name__', 'target')}"
        self.dist = SimpleNamespace(metadata={"Name": "test-plugin"}, version="1.0")
        self._target = target
        self.load_count = 0

    def load(self) -> object:
        self.load_count += 1
        return self._target


def _document(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": "1", "plugins": list(entries)}


def _entry(
    plugin_id: str = "configured-1",
    *,
    plugin_key: str = "test.configured",
    enabled: bool = True,
    marker: str = "first",
) -> dict[str, Any]:
    return {
        "plugin_id": plugin_id,
        "plugin_key": plugin_key,
        "enabled": enabled,
        "configuration": {"marker": marker, "nested": {"values": [1]}},
    }


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, *entry_points: _FakeEntryPoint) -> None:
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: entry_points,
    )


def _definition(name: str, *, subagents: tuple[SubagentDefinition, ...] = ()) -> AgentDefinition[str]:
    return AgentDefinition(
        agent=AgentSpec(model="test", name=name),
        output_type=str,
        subagents=subagents,
    )


def test_disabled_environment_ignores_other_sources_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HARNESS_PLUGIN_CONFIG_ENABLED_ENV, "false")
    monkeypatch.setenv(HARNESS_PLUGIN_CONFIG_JSON_ENV, "not-json")
    monkeypatch.setenv(HARNESS_PLUGIN_CONFIG_FILE_ENV, "/private/missing/secret.json")
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("disabled builder must not scan metadata")),
    )

    builder = HarnessBuilder()

    assert isinstance(builder, HarnessBuilder)


@pytest.mark.parametrize("value", ["", " true", "true ", "enabled", "2"])
def test_environment_rejects_invalid_enable_value(value: str) -> None:
    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext.from_environment(environ={HARNESS_PLUGIN_CONFIG_ENABLED_ENV: value})

    assert exc_info.value.code == "plugin_configuration_enablement_invalid"


def test_enabled_environment_requires_a_source(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext.from_environment(environ={HARNESS_PLUGIN_CONFIG_ENABLED_ENV: "true"})

    assert exc_info.value.code == "plugin_configuration_source_missing"
    assert DEFAULT_HARNESS_PLUGIN_CONFIG_FILE not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    assert str(tmp_path) not in "".join(traceback.format_exception(exc_info.value))


def test_environment_inline_json_precedes_file_source(tmp_path) -> None:
    invalid_file = tmp_path / "private-secret-name.json"
    invalid_file.write_text("not-json", encoding="utf-8")
    inline = json.dumps(_document(_entry()))

    context = HarnessBuildContext.from_environment(
        environ={
            HARNESS_PLUGIN_CONFIG_ENABLED_ENV: "ON",
            HARNESS_PLUGIN_CONFIG_JSON_ENV: inline,
            HARNESS_PLUGIN_CONFIG_FILE_ENV: str(invalid_file),
        }
    )

    assert context.configured_plugins_enabled is True
    assert context.plugin_configuration is not None
    assert [entry.plugin_id for entry in context.plugin_configuration.enabled_plugins] == ["configured-1"]


def test_environment_loads_explicit_and_default_files(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    explicit = tmp_path / "plugins.json"
    explicit.write_text(json.dumps(_document(_entry("explicit-1"))), encoding="utf-8")
    explicit_context = HarnessBuildContext.from_environment(
        environ={
            HARNESS_PLUGIN_CONFIG_ENABLED_ENV: "yes",
            HARNESS_PLUGIN_CONFIG_FILE_ENV: str(explicit),
        }
    )

    default = tmp_path / DEFAULT_HARNESS_PLUGIN_CONFIG_FILE
    default.write_text(
        """schema_version: "1"
plugins:
  - plugin_id: default-1
    plugin_key: test.configured
    enabled: true
    configuration:
      marker: from-yaml
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    default_context = HarnessBuildContext.from_environment(environ={HARNESS_PLUGIN_CONFIG_ENABLED_ENV: "1"})

    assert explicit_context.plugin_configuration is not None
    assert explicit_context.plugin_configuration.plugins[0].plugin_id == "explicit-1"
    assert default_context.plugin_configuration is not None
    assert default_context.plugin_configuration.plugins[0].plugin_id == "default-1"
    assert default_context.plugin_configuration.plugins[0].configuration == {"marker": "from-yaml"}


def test_yaml_configuration_supports_plugin_parameters_and_disabled_entries() -> None:
    context = HarnessBuildContext.from_yaml(
        """schema_version: "1"
plugins:
  - plugin_id: configured-1
    plugin_key: test.configured
    enabled: true
    configuration:
      marker: yaml
      nested:
        values: [1, 2]
  - plugin_id: disabled-1
    plugin_key: test.configured
    enabled: false
    configuration:
      marker: disabled
"""
    )

    assert context.plugin_configuration is not None
    assert [entry.plugin_id for entry in context.plugin_configuration.enabled_plugins] == ["configured-1"]
    assert context.plugin_configuration.plugins[0].configuration == {
        "marker": "yaml",
        "nested": {"values": [1, 2]},
    }


@pytest.mark.parametrize(
    "value",
    [
        'schema_version: "1"\nplugins: &items []\n',
        'schema_version: "1"\nplugins: *items\n',
        'schema_version: "1"\nschema_version: "1"\nplugins: []\n',
        'schema_version: !!str "1"\nplugins: []\n',
        'schema_version: "1"\nplugins: []\n---\nschema_version: "1"\nplugins: []\n',
        'schema_version: "1"\nplugins: []\nextra: 2026-01-01\n',
    ],
)
def test_yaml_configuration_rejects_unsafe_or_non_json_features(value: str) -> None:
    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext.from_yaml(value)

    assert exc_info.value.code == "plugin_configuration_invalid"


@pytest.mark.parametrize("collection", ["sequence", "mapping"])
def test_yaml_configuration_stops_large_collections_during_composition(collection: str) -> None:
    if collection == "sequence":
        payload = "[" + ",".join("0" for _ in range(10_100)) + "]"
    else:
        payload = "{" + ",".join(f"key-{index}: 0" for index in range(5_100)) + "}"
    source = f"""schema_version: "1"
plugins:
  - plugin_id: configured-1
    plugin_key: test.configured
    enabled: true
    configuration:
      value: {payload}
"""

    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext.from_yaml(source)

    assert exc_info.value.code == "plugin_configuration_too_large"


def test_yaml_configuration_stops_deep_nesting_during_composition() -> None:
    nested = "[" * 70 + "0" + "]" * 70
    source = f"""schema_version: "1"
plugins:
  - plugin_id: configured-1
    plugin_key: test.configured
    enabled: true
    configuration:
      value: {nested}
"""

    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext.from_yaml(source)

    assert exc_info.value.code == "plugin_configuration_too_large"


def test_yaml_configuration_stops_oversized_scalar_during_composition() -> None:
    source = f"""schema_version: "1"
plugins:
  - plugin_id: configured-1
    plugin_key: test.configured
    enabled: true
    configuration:
      value: {"x" * 300_000}
"""

    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext.from_yaml(source)

    assert exc_info.value.code == "plugin_configuration_too_large"


def test_yaml_configuration_traceback_suppresses_source_content() -> None:
    source = """schema_version: "1"
plugins:
  - plugin_id: configured-1
    plugin_key: test.configured
    enabled: true
    configuration:
      token: super-secret: malformed
"""

    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext.from_yaml(source)

    formatted = "".join(traceback.format_exception(exc_info.value))
    assert exc_info.value.code == "plugin_configuration_invalid"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    assert "super-secret" not in formatted
    assert "token:" not in formatted


def test_configuration_file_rejects_toml_and_unknown_suffixes(tmp_path) -> None:
    source = tmp_path / "harness-plugins.toml"
    source.write_text('schema_version = "1"\nplugins = []\n', encoding="utf-8")

    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext.from_file(source)

    assert exc_info.value.code == "plugin_configuration_format_unsupported"


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        "[]",
        '{"schema_version":"1","plugins":[],"extra":true}',
        '{"schema_version":"1","plugins":[],"plugins":[]}',
        '{"schema_version":"1","plugins":[{"plugin_id":"one","plugin_key":"test.configured",'
        '"enabled":"true","configuration":{}}]}',
        '{"schema_version":"1","plugins":[{"plugin_id":"one","plugin_key":"test.configured",'
        '"enabled":true,"configuration":{"value":NaN}}]}',
    ],
)
def test_inline_configuration_rejects_non_strict_documents(value: str) -> None:
    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext.from_json(value)

    assert exc_info.value.code == "plugin_configuration_invalid"


def test_configuration_rejects_unsupported_version_and_duplicate_ids() -> None:
    with pytest.raises(PluginError) as version_exc:
        HarnessBuildContext.from_configuration({"schema_version": "2", "plugins": []})
    with pytest.raises(PluginError) as duplicate_exc:
        HarnessBuildContext.from_configuration(_document(_entry("same"), _entry("same")))

    assert version_exc.value.code == "plugin_configuration_version_unsupported"
    assert duplicate_exc.value.code == "plugin_configuration_plugin_id_duplicate"
    assert duplicate_exc.value.details == {"plugin_id": "same"}


def test_configuration_rejects_non_json_and_oversized_values() -> None:
    non_json = _document(_entry())
    non_json["plugins"][0]["configuration"] = {"value": object()}
    oversized = _document(_entry())
    oversized["plugins"][0]["configuration"] = {"value": "x" * (1024 * 1024)}

    with pytest.raises(PluginError) as non_json_exc:
        HarnessBuildContext.from_configuration(non_json)
    with pytest.raises(PluginError) as oversized_exc:
        HarnessBuildContext.from_configuration(oversized)

    assert non_json_exc.value.code == "plugin_configuration_invalid"
    assert oversized_exc.value.code == "plugin_configuration_too_large"


@pytest.mark.parametrize("namespace", ["", " padded", "padded ", "x" * 201])
def test_build_context_rejects_invalid_extension_namespace(namespace: str) -> None:
    with pytest.raises(PluginError) as exc_info:
        HarnessBuildContext(extensions={namespace: {}})

    assert exc_info.value.code == "plugin_configuration_invalid"


def test_explicit_context_bypasses_invalid_ambient_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(HARNESS_PLUGIN_CONFIG_ENABLED_ENV, "invalid")
    context = HarnessBuildContext()

    builder = HarnessBuilder(build_context=context)

    assert isinstance(builder, HarnessBuilder)


def test_builder_call_site_override_can_enable_or_disable_ambient_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _FakeEntryPoint("test.configured", _ConfiguredFactory)
    _patch_entry_points(monkeypatch, selected)
    monkeypatch.setenv(HARNESS_PLUGIN_CONFIG_ENABLED_ENV, "false")
    monkeypatch.setenv(HARNESS_PLUGIN_CONFIG_JSON_ENV, json.dumps(_document(_entry())))

    enabled_builder = HarnessBuilder(configured_plugins_enabled=True)
    monkeypatch.setenv(HARNESS_PLUGIN_CONFIG_ENABLED_ENV, "true")
    monkeypatch.setenv(HARNESS_PLUGIN_CONFIG_JSON_ENV, "not-json")
    disabled_builder = HarnessBuilder(configured_plugins_enabled=False)

    assert isinstance(enabled_builder, HarnessBuilder)
    assert isinstance(disabled_builder, HarnessBuilder)
    assert selected.load_count == 1


def test_builder_call_site_override_can_disable_an_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("disabled explicit context must not scan metadata")),
    )
    context = HarnessBuildContext.from_configuration(_document(_entry()))

    executable = HarnessBuilder(
        build_context=context,
        configured_plugins_enabled=False,
    ).build(_definition("disabled"))

    assert executable._plugins == ()


def test_builder_rejects_enabling_explicit_context_without_configuration() -> None:
    with pytest.raises(PluginError) as exc_info:
        HarnessBuilder(build_context=HarnessBuildContext(), configured_plugins_enabled=True)

    assert exc_info.value.code == "plugin_configuration_invalid"


def test_builder_applies_enabled_entries_to_each_definition_with_fresh_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ConfiguredFactory.contexts.clear()
    _ConfiguredFactory.plugins.clear()
    selected = _FakeEntryPoint("test.configured", _ConfiguredFactory)
    unselected = _FakeEntryPoint("test.disabled", RuntimeError("must not load"))
    _patch_entry_points(monkeypatch, unselected, selected)
    source = _document(
        _entry("configured-1", marker="one"),
        _entry("disabled-1", plugin_key="test.disabled", enabled=False),
        _entry("configured-2", marker="two"),
    )
    extensions = {"foundation": {"deployment": "primary"}}
    context = HarnessBuildContext.from_configuration(source, extensions=extensions)
    source["plugins"][0]["configuration"]["nested"]["values"].append(2)
    extensions["foundation"]["deployment"] = "mutated"
    builder = HarnessBuilder(build_context=context)
    assert context.plugin_configuration is not None
    context_nested = context.plugin_configuration.plugins[0].configuration["nested"]
    context_foundation = context.extensions["foundation"]
    assert isinstance(context_nested, dict)
    assert isinstance(context_nested["values"], list)
    assert isinstance(context_foundation, dict)
    context_nested["values"].append(3)
    context_foundation["deployment"] = "mutated-after-builder"
    child = _definition("child")
    root = _definition(
        "root",
        subagents=(SubagentDefinition(name="child", description="A child.", agent=child),),
    )

    executable = builder.build(root)

    assert selected.load_count == 1
    assert unselected.load_count == 0
    assert len(_ConfiguredFactory.contexts) == 4
    assert len({id(plugin) for plugin in _ConfiguredFactory.plugins}) == 4
    assert [context.plugin_id for context in _ConfiguredFactory.contexts] == [
        "configured-1",
        "configured-2",
        "configured-1",
        "configured-2",
    ]
    assert all(
        factory_context.extensions == {"foundation": {"deployment": "primary"}}
        for factory_context in _ConfiguredFactory.contexts
    )
    assert _ConfiguredFactory.contexts[0].configuration["nested"] == {"values": [1]}
    assert [plugin.plugin_id for plugin in executable._plugins] == ["configured-1", "configured-2"]
    child_executable = executable.subagents.require("child").executable
    assert [plugin.plugin_id for plugin in child_executable._plugins] == ["configured-1", "configured-2"]


def test_configured_plugins_append_after_direct_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    _ConfiguredFactory.contexts.clear()
    _ConfiguredFactory.plugins.clear()
    selected = _FakeEntryPoint("test.configured", _ConfiguredFactory)
    _patch_entry_points(monkeypatch, selected)
    context = HarnessBuildContext.from_configuration(_document(_entry("configured-1")))
    direct = _ConfiguredPlugin("direct-1", "direct")

    executable = HarnessBuilder(build_context=context).build(
        AgentSpec(model="test", name="root"),
        output_type=str,
        plugins=(direct,),
    )

    assert [plugin.plugin_id for plugin in executable._plugins] == ["direct-1", "configured-1"]


def test_direct_and_configured_plugin_id_collision_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = _FakeEntryPoint("test.configured", _ConfiguredFactory)
    _patch_entry_points(monkeypatch, selected)
    context = HarnessBuildContext.from_configuration(_document(_entry("same-1")))

    with pytest.raises(PluginError) as exc_info:
        HarnessBuilder(build_context=context).build(
            AgentSpec(model="test", name="root"),
            output_type=str,
            plugins=(_ConfiguredPlugin("same-1", "direct"),),
        )

    assert exc_info.value.code == "plugin_id_duplicate"
