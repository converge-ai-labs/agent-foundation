"""Fresh-process import boundaries and native Python 3.15 reification."""

from __future__ import annotations

import subprocess
import sys
from textwrap import dedent

import pytest

MODES = [False, True] if sys.version_info >= (3, 15) else [False]


def _run(script: str, *, eager: bool = False) -> None:
    prefix = "import sys; sys.set_lazy_imports_filter(lambda *args: False)\n" if eager else ""
    result = subprocess.run([sys.executable, "-c", prefix + dedent(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("eager", MODES)
def test_specialized_facades_are_inert_and_preserve_exports(eager: bool) -> None:
    _run(
        """
        import sys
        from importlib import import_module
        import a13n_harness.models as models
        import a13n_harness.filters as filters
        assert not {'pydantic_ai', 'PIL.Image', 'pydantic_monty'} & sys.modules.keys()
        for facade in (models, filters):
            assert set(facade.__all__) == {name for names in facade._EXPORTS.values() for name in names}
            for module, names in facade._EXPORTS.items():
                for name in names:
                    assert name in dir(facade)
                    assert getattr(facade, name) is getattr(import_module(module), name)
                    assert getattr(facade, name) is vars(facade)[name]
            try:
                facade.not_an_export
            except AttributeError:
                pass
            else:
                raise AssertionError('Unknown export must fail')
        """,
        eager=eager,
    )


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Native lazy imports require Python 3.15")
@pytest.mark.parametrize("eager", [False, True])
def test_native_builder_and_feature_declarations(eager: bool) -> None:
    _run(
        f"""
        import sys
        from a13n_harness import HarnessBuilder
        assert isinstance(HarnessBuilder, type)
        from a13n_harness.capabilities.codeact import CodeActCapability
        from a13n_harness.filters.image import ImageFilterCapability
        CodeActCapability()
        ImageFilterCapability()
        assert ('pydantic_monty' in sys.modules) is {eager!r}
        assert ('PIL.Image' in sys.modules) is {eager!r}
        assert ('a13n_harness.execution' in sys.modules) is {eager!r}
        """,
        eager=eager,
    )


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Native lazy imports require Python 3.15")
def test_native_feature_reification_stays_in_worker_and_retries_failure() -> None:
    _run(
        """
        import asyncio
        import base64
        import sys
        import threading
        import anyio.to_thread
        from pydantic_ai import BinaryContent
        from a13n_harness.capabilities.codeact import _prepare_runtime
        from a13n_harness.filters.image import _prepare_binary_image
        from a13n_harness.image_input import ImageInputPolicy

        assert 'pydantic_monty' not in sys.modules
        assert 'PIL.Image' not in sys.modules
        imported_on = {}
        def audit(event, args):
            if event == 'import' and args[0] in ('pydantic_monty', 'PIL.Image'):
                imported_on[args[0]] = threading.get_ident()
        sys.addaudithook(audit)

        class FailMonty:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'pydantic_monty':
                    raise ImportError('sandbox import failed')
        failure = FailMonty()

        async def main():
            sys.meta_path.insert(0, failure)
            try:
                await anyio.to_thread.run_sync(_prepare_runtime)
            except ImportError as exc:
                assert 'sandbox import failed' in str(exc)
            else:
                raise AssertionError('Import failure must propagate')
            finally:
                sys.meta_path.remove(failure)
            assert 'a13n_harness.codeact.runtime' not in sys.modules
            states = await asyncio.gather(*(
                anyio.to_thread.run_sync(_prepare_runtime) for _ in range(4)
            ))
            assert all(state is states[0] for state in states)
            image = BinaryContent(
                data=base64.b64decode(
                    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='
                ), media_type='image/png',
            )
            await anyio.to_thread.run_sync(_prepare_binary_image, image, ImageInputPolicy())
            assert set(imported_on) == {'pydantic_monty', 'PIL.Image'}
            assert all(thread != threading.get_ident() for thread in imported_on.values())
        asyncio.run(main())
        """
    )


@pytest.mark.parametrize("eager", MODES)
def test_native_tool_identity_does_not_load_mcp_and_preserves_mcp_sources(eager: bool) -> None:
    _run(
        """
        import asyncio
        import sys
        from dataclasses import replace
        from pydantic_ai import RunContext
        from pydantic_ai.models.test import TestModel
        from pydantic_ai.toolsets import FunctionToolset
        from pydantic_ai.usage import RunUsage
        from a13n_harness.errors import DefinitionError
        from a13n_harness.tools.identity import identify_tool, tool_identity

        def execute() -> str:
            return 'ok'

        async def main():
            source = FunctionToolset([execute], id='business').renamed(
                {'renamed': 'execute'}
            ).prefixed('visible')
            ctx = RunContext(deps=None, model=TestModel(), usage=RunUsage())
            tool = (await source.get_tools(ctx))['visible_renamed']
            identified = identify_tool(tool)
            assert tool_identity(identified.tool_def).tool_id == 'tool/business/execute'
            assert 'pydantic_ai.mcp' not in sys.modules
            assert 'fastmcp' not in sys.modules

            from pydantic_ai.mcp import MCPToolset
            mcp = MCPToolset('http://localhost:9999/mcp', id='server')
            wrapped = mcp.renamed({'renamed': 'execute'}).prefixed('visible')
            identified = identify_tool(replace(tool, toolset=wrapped))
            assert tool_identity(identified.tool_def).tool_id == 'mcp/server/execute'
            assert identify_tool(identified) is identified
            anonymous = MCPToolset('http://localhost:9999/mcp')
            try:
                identify_tool(replace(tool, toolset=anonymous))
            except DefinitionError as exc:
                assert exc.code == 'tool_source_id_required'
            else:
                raise AssertionError('MCP source must have a stable id')
        asyncio.run(main())
        """,
        eager=eager,
    )
