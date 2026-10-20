"""The public client surface does not select a transport on import."""

import subprocess
import sys

import pytest

MODES = [False, True] if sys.version_info >= (3, 15) else [False]


def _run(script: str, eager: bool = False) -> subprocess.CompletedProcess[str]:
    if eager:
        script = "import sys; sys.set_lazy_imports_filter(lambda *args: False)\n" + script
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("eager", MODES)
def test_protocol_and_session_imports_do_not_select_transports(eager: bool) -> None:
    script = """
import sys
import a13n_envd_client as client
assert not {'httpx2', 'pydantic', 'websockets'} & sys.modules.keys()
from a13n_envd_client import EIPSession
from a13n_envd_client.session import EIPSession as implementation
assert EIPSession is implementation is client.EIPSession
assert not {'httpx2', 'websockets', 'a13n_envd_client.http', 'a13n_envd_client.stdio', 'a13n_envd_client.websocket'} & sys.modules.keys()
from a13n_envd_client import HttpTransport
from a13n_envd_client.http import HttpTransport as http_implementation
assert HttpTransport is http_implementation
assert 'httpx2' in sys.modules
assert 'a13n_envd_client.websocket' not in sys.modules
"""
    result = _run(script, eager)
    assert result.returncode == 0, result.stderr


def test_public_exports_are_cached_and_unknown_names_raise_attribute_error() -> None:
    import a13n_envd_client as client

    for name in client.__all__:
        assert getattr(client, name) is getattr(client, name)
        assert name in dir(client)
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = client.not_a_client_export


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Native lazy imports require Python 3.15")
@pytest.mark.parametrize("eager", MODES)
def test_generated_surface_uses_native_lazy_imports(eager: bool) -> None:
    script = """
import sys
import a13n_envd_client.eip.v1 as protocol
lazy = sys.get_lazy_imports_filter() is None
assert ('a13n_envd_client.eip.v1.models' not in sys.modules) == lazy
from a13n_envd_client.eip.v1 import DataFrame
from a13n_envd_client.eip.v1.data_frame import DataFrame as implementation
assert DataFrame is implementation
assert ('a13n_envd_client.eip.v1.models' not in sys.modules) == lazy
"""
    result = _run(script, eager)
    assert result.returncode == 0, result.stderr


def test_failed_export_can_be_retried_and_concurrent_resolution_preserves_identity() -> None:
    script = """
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib.abc import MetaPathFinder
import a13n_envd_client as client
class BlockHttp(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'a13n_envd_client.http':
            raise ImportError('test transport failure')
blocker = BlockHttp()
sys.meta_path.insert(0, blocker)
try:
    client.HttpTransport
except ImportError as error:
    assert str(error) == 'test transport failure'
else:
    raise AssertionError('Import failure must propagate')
assert 'HttpTransport' not in vars(client)
sys.meta_path.remove(blocker)
with ThreadPoolExecutor(max_workers=4) as workers:
    values = list(workers.map(lambda _: client.HttpTransport, range(12)))
assert all(value is values[0] for value in values)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
