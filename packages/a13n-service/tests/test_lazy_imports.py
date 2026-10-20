"""Fresh-process command, assembly and selected-backend import boundaries."""

import json
import os
import subprocess
import sys
import textwrap

import pytest


def _child(source: str, *, eager: bool = False) -> str:
    prefix = "import sys; sys.set_lazy_imports_filter(lambda *args: False)\n" if eager else ""
    result = subprocess.run(
        [sys.executable, "-c", prefix + textwrap.dedent(source)],
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("A13N_")},
            "PYTHONHASHSEED": "0",
        },
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.skipif(sys.version_info < (3, 15), reason="native lazy imports require Python 3.15")
def test_native_cli_help_does_not_assemble_the_service() -> None:
    _child("""
        import sys
        from click.testing import CliRunner
        from a13n_service.cli import main
        for command in [[], ['run'], ['migrate'], ['bootstrap'], ['user']]:
            result = CliRunner().invoke(main, [*command, '--help'])
            assert result.exit_code == 0, result.output
        assert 'a13n_service.app' not in sys.modules
        assert 'a13n_service.distribution' not in sys.modules
        assert 'a13n_service.runs.execute' not in sys.modules
        assert 'aiobotocore' not in sys.modules
    """)


@pytest.mark.skipif(sys.version_info < (3, 15), reason="native lazy imports require Python 3.15")
@pytest.mark.parametrize("role", ["control", "worker", "all"])
def test_native_roles_keep_full_assembly_validation(role: str) -> None:
    _child(f"""
        import sys
        from dataclasses import replace
        from a13n_service.app import build_app, check_schema, open_storage
        from a13n_service.distribution import OSS
        from a13n_service.infra import db
        from a13n_service.settings import Settings
        assert check_schema is db.check_schema
        assert open_storage is db.open_storage
        app = build_app(role={role!r}, settings=Settings())
        assert 'a13n_service.runs.execute' not in sys.modules
        assert 'aiobotocore' not in sys.modules
        assert ('a13n_service.mcp' in sys.modules) == ({role!r} != 'worker')
        assert bool(app.openapi_url) == ({role!r} != 'worker')
        for invalid, message in [
            (replace(OSS, routers=OSS.routers + OSS.routers[:1]), 'Duplicate route'),
            (replace(OSS, tables=OSS.tables + OSS.tables[:1]), 'Duplicate table'),
        ]:
            try:
                build_app(invalid, role={role!r}, settings=Settings())
            except ValueError as error:
                assert message in str(error), error
            else:
                raise AssertionError(message)
    """)


def test_composed_schema_matches_eager_imports() -> None:
    source = """
        import hashlib, json
        from sqlalchemy.schema import AddConstraint, CreateColumn, CreateIndex
        from sqlalchemy.dialects.postgresql import dialect
        from a13n_service.app import build_app
        from a13n_service.distribution import OSS
        from a13n_service.migrations.runner import heads
        from a13n_service.settings import Settings
        document = {
            'openapi': build_app(settings=Settings()).openapi(),
            # Constraints and indexes are sets; compare their SQL independently of iteration order.
            'tables': {t.name: {
                'columns': [str(CreateColumn(c).compile(dialect=dialect())) for c in t.columns],
                'constraints': sorted(str(AddConstraint(c).compile(dialect=dialect())) for c in t.constraints),
                'indexes': sorted(str(CreateIndex(i).compile(dialect=dialect())) for i in t.indexes),
            } for t in OSS.metadata().sorted_tables},
            'rules': OSS.rules(),
            'heads': heads(OSS),
        }
        print(json.dumps({'sha256': hashlib.sha256(
            json.dumps(document, sort_keys=True).encode()).hexdigest()}))
    """
    normal = json.loads(_child(source))
    if sys.version_info >= (3, 15):
        assert normal == json.loads(_child(source, eager=True))
    assert len(normal["sha256"]) == 64


@pytest.mark.skipif(sys.version_info < (3, 15), reason="native lazy imports require Python 3.15")
def test_selected_executor_and_s3_load_off_loop_and_retry() -> None:
    _child("""
        import asyncio, importlib.abc, sys, threading
        from contextlib import AsyncExitStack
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from anyio.to_thread import run_sync
        from a13n_service.app import _prepare_execution, open_objects
        from a13n_service.settings import Objects
        imports = {}
        def audit(event, args):
            if event == 'import' and args[0] in (
                'a13n_service.runs.execute', 'aiobotocore.session'
            ):
                imports[args[0]] = threading.get_ident()
        sys.addaudithook(audit)
        class Broken(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'a13n_service.runs.execute':
                    raise ImportError('executor unavailable')
        async def main():
            owner = threading.get_ident()
            blocker = Broken()
            sys.meta_path.insert(0, blocker)
            try:
                await run_sync(_prepare_execution)
            except ImportError as error:
                assert str(error) == 'executor unavailable'
            else:
                raise AssertionError('executor failure must propagate')
            finally:
                sys.meta_path.remove(blocker)
            executors = await asyncio.gather(*(run_sync(_prepare_execution) for _ in range(4)))
            assert all(executor is executors[0] for executor in executors)
            assert imports['a13n_service.runs.execute'] != owner
            with TemporaryDirectory() as root:
                async with AsyncExitStack() as stack:
                    local = await open_objects(stack, Objects(root=Path(root)))
                    await local.put('sample', b'data', content_type='text/plain')
                    assert await local.get('sample') == b'data'
                    assert 'aiobotocore.session' not in sys.modules
                    remote = await open_objects(stack, Objects(
                        backend='s3', bucket='test', region='us-east-1',
                        endpoint_url='http://127.0.0.1:1',
                        access_key_id='test', secret_access_key='test',
                    ))
                    assert remote.bucket == 'test'
                    assert imports['aiobotocore.session'] != owner
        asyncio.run(main())
    """)
