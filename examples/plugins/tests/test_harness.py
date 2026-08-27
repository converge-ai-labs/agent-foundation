from __future__ import annotations

import asyncio
import sys

import pytest
from a13n_harness import (
    HarnessPluginFactoryContext,
    PluginError,
    RunBindings,
    build_harness_plugin_factory_catalog,
    discover_harness_plugin_factory_references,
)

from a13n_plugin_examples.demo_harness import (
    PLUGIN_KEY,
    build_code_demo_agent,
    build_configured_demo_agent,
    run_harness_code_demo,
    run_harness_entrypoint_demo,
)
from a13n_plugin_examples.records import RunObservation

PLUGIN_MODULE = "a13n_plugin_examples.harness"


def test_harness_entrypoint_metadata_is_lazy_and_selection_is_explicit() -> None:
    assert PLUGIN_MODULE not in sys.modules

    references = discover_harness_plugin_factory_references()
    assert PLUGIN_KEY in {reference.plugin_key for reference in references}
    assert PLUGIN_MODULE not in sys.modules

    catalog = build_harness_plugin_factory_catalog(plugin_keys=(PLUGIN_KEY,))
    assert PLUGIN_MODULE in sys.modules
    registration = catalog.registrations[0]
    assert registration.plugin_key == PLUGIN_KEY
    assert registration.import_target == ("a13n_plugin_examples.harness:RunRecorderPluginFactory")

    plugin = catalog.create_plugin(
        HarnessPluginFactoryContext(
            plugin_key=PLUGIN_KEY,
            plugin_id="recorder-selected",
            configuration={"count_events": True},
            extensions={"example": {"test": True}},
        )
    )
    from a13n_plugin_examples.harness import RunRecorderPlugin

    assert isinstance(plugin, RunRecorderPlugin)
    assert plugin.plugin_id == "recorder-selected"
    assert plugin.observations == ()


def test_harness_explicit_concrete_plugin_needs_no_metadata_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "a13n_harness.plugin_factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("code mode must not scan metadata")),
    )
    observations: list[RunObservation] = []

    executable = build_code_demo_agent(observations)

    assert executable.definition.plugins[0].plugin_id == "recorder-code"
    asyncio.run(executable.close())


@pytest.mark.parametrize("count_events", [1, "true"])
def test_harness_plugin_factory_rejects_invalid_configuration(count_events: str | int) -> None:
    catalog = build_harness_plugin_factory_catalog(plugin_keys=(PLUGIN_KEY,))

    with pytest.raises(PluginError) as exc_info:
        catalog.create_plugin(
            HarnessPluginFactoryContext(
                plugin_key=PLUGIN_KEY,
                plugin_id="recorder-invalid",
                configuration={"count_events": count_events},
                extensions={},
            )
        )

    assert exc_info.value.code == "plugin_factory_failed"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_harness_configuration_keeps_factory_objects_out_of_agent_definition() -> None:
    executable = build_configured_demo_agent()

    assert executable.definition.plugins == ()
    asyncio.run(executable.close())


def test_harness_entrypoint_demo_runs_factory_plugin() -> None:
    result = asyncio.run(run_harness_entrypoint_demo())

    assert result.selection_mode == "entrypoint"
    assert result.plugin_id == "recorder-entrypoint"
    assert result.output == "offline model response"
    assert result.observation.status == "completed"
    assert result.observation.run_id == result.run_id
    assert result.observation.event_count > 0


def test_harness_code_demo_runs_concrete_plugin() -> None:
    result = asyncio.run(run_harness_code_demo())

    assert result.selection_mode == "code"
    assert result.plugin_id == "recorder-code"
    assert result.output == "offline model response"
    assert result.observation.status == "completed"
    assert result.observation.run_id == result.run_id
    assert result.observation.event_count > 0


def test_harness_plugin_creates_isolated_state_for_concurrent_runs() -> None:
    observations: list[RunObservation] = []
    executable = build_code_demo_agent(observations)

    async def run_both() -> tuple[str, str]:
        async with executable:
            first, second = await asyncio.gather(
                executable.run("first", bindings=RunBindings.local()),
                executable.run("second", bindings=RunBindings.local()),
            )
        return first.run_id, second.run_id

    run_ids = asyncio.run(run_both())

    assert run_ids[0] != run_ids[1]
    assert {observation.run_id for observation in observations} == set(run_ids)
    assert all(observation.status == "completed" for observation in observations)
