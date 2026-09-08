"""Startup resolves trusted installed entry points before any role becomes ready."""

from importlib.metadata import EntryPoint
from threading import get_ident

import pytest
from a13n_harness.errors import PluginError
from a13n_harness.plugin_factories import build_harness_plugin_factory_catalog
from a13n_service.app import Components, create_app

from tests.agents.test_installed_plugins import InstalledFactory


@pytest.mark.anyio
async def test_installed_catalog_loads_once_off_loop_and_is_shared(local_settings, tmp_path, monkeypatch):
    import a13n_harness.plugin_factories as factories

    event_loop_thread = get_ident()
    discovery_threads = []

    def entry_points():
        discovery_threads.append(get_ident())
        return (
            EntryPoint(
                name="test.audit",
                value="tests.agents.test_installed_plugins:InstalledFactory",
                group="a13n_harness.plugins",
            ),
        )

    monkeypatch.setattr(factories, "_entry_points", entry_points)
    app = create_app(
        local_settings(tmp_path, plugin_keys=("test.audit",), pricing_auto_update=False, observability_tracing=False)
    )
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        catalog = runtime.worker.execution_loop._catalog
        assert tuple(catalog) == ("test.audit",)
        assert runtime.control.agents.commands._resolver._plugin_catalog is catalog
        assert runtime.status.startup_complete
    assert len(discovery_threads) == 1
    assert discovery_threads[0] != event_loop_thread


@pytest.mark.anyio
async def test_missing_installed_plugin_prevents_startup(local_settings, tmp_path):
    app = create_app(
        local_settings(
            tmp_path, plugin_keys=("missing.factory",), pricing_auto_update=False, observability_tracing=False
        )
    )
    with pytest.raises(PluginError, match="not found"):
        async with app.router.lifespan_context(app):
            pytest.fail("Invalid catalog must not become ready")


@pytest.mark.anyio
async def test_distribution_can_supply_explicit_installed_catalog(local_settings, tmp_path):
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(InstalledFactory(),))
    app = create_app(
        local_settings(tmp_path, pricing_auto_update=False, observability_tracing=False),
        components=Components(plugin_factory_catalog=catalog),
    )
    async with app.router.lifespan_context(app):
        assert app.state.runtime.worker.execution_loop._catalog is catalog
