"""Native lazy imports retain App readiness and selected SDK lifetimes."""

from __future__ import annotations

import os
import subprocess
import sys
from textwrap import dedent

import pytest

pytestmark = pytest.mark.skipif(sys.version_info < (3, 15), reason="Native lazy imports require Python 3.15")


def _run(script: str, *, eager: bool = False) -> None:
    prefix = "import sys; sys.set_lazy_imports_filter(lambda *args: False)\n" if eager else ""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("A13N_")}
    result = subprocess.run(
        [sys.executable, "-c", prefix + dedent(script)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("eager", [False, True])
def test_facade_exports_keep_identity(eager: bool) -> None:
    _run(
        f"""
        import sys
        import a13n_harness_ui.composition as composition
        import a13n_harness_ui.storage as storage
        assert ('a13n_harness_ui.composition.reconstruction' in sys.modules) is {eager!r}
        assert ('a13n_harness_ui.storage.database' in sys.modules) is {eager!r}
        from a13n_harness_ui.composition.reconstruction import AgentReconstructor
        from a13n_harness_ui.storage.database import open_database
        assert composition.AgentReconstructor is AgentReconstructor
        assert storage.open_database is open_database
        for facade in (composition, storage):
            assert all(name in dir(facade) for name in facade.__all__)
            assert all(getattr(facade, name) is getattr(facade, name) for name in facade.__all__)
        """,
        eager=eager,
    )


def test_management_handler_import_does_not_load_app() -> None:
    _run(
        """
        import sys
        import a13n_harness_ui.cli_runtime
        assert 'a13n_harness_ui.app' not in sys.modules
        assert 'fastmcp' not in sys.modules
        """
    )


@pytest.mark.parametrize("webui", [False, True])
def test_app_ready_and_selected_mcp_import_thread(webui: bool) -> None:
    _run(
        f"""
        import asyncio
        import sys
        import tempfile
        import threading
        from pathlib import Path
        from a13n_harness_ui.interactive.startup import _load_runtime

        imported_on = {{}}
        def audit(event, args):
            if event == 'import' and args[0] in ('fastmcp', 'a13n_harness_ui.app'):
                imported_on[args[0]] = threading.get_ident()
        sys.addaudithook(audit)

        async def main():
            factory = await asyncio.to_thread(_load_runtime)
            assert callable(factory)
            assert 'fastmcp' not in sys.modules
            from a13n_harness_ui.app import open_harness_ui_app
            from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
            with tempfile.TemporaryDirectory() as tmp:
                settings = HarnessUiSettings(
                    storage=StorageSettings(data_root=Path(tmp)), pricing_auto_update=False,
                )
                async with open_harness_ui_app(
                    settings, host_mode={"webui" if webui else "local"!r},
                    mcp_input_enabled=True, instrumentation=None,
                ) as app:
                    status = await app.status()
                    assert status.candidate_error_code is None
                    assert ('fastmcp' in sys.modules) is {webui!r}
                    assert 'pydantic_monty' not in sys.modules
                    assert 'PIL.Image' not in sys.modules
                    from a13n_harness_ui.composition.models import ResolvedMcpRecipe
                    from a13n_harness_ui.configuration.models import McpCommandTransport
                    from a13n_harness_ui.mcp_adapters import prepare_mcp_transport
                    recipe = ResolvedMcpRecipe(
                        server_id='test', transport=McpCommandTransport(command=sys.executable),
                    )
                    transport, _, timeout = await prepare_mcp_transport(recipe, None)
                    assert timeout == 30
                    await transport.close()
            assert set(imported_on) == {{'a13n_harness_ui.app', 'fastmcp'}}
            assert all(thread != threading.get_ident() for thread in imported_on.values())
        asyncio.run(main())
        """
    )
