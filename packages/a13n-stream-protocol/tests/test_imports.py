"""Transport helpers are independent of the Harness observation adapter."""

import subprocess
import sys

import pytest

MODES = [False, True] if sys.version_info >= (3, 15) else [False]


@pytest.mark.parametrize("eager", MODES)
def test_fragment_import_does_not_load_observer(eager: bool) -> None:
    script = """
import sys
import a13n_stream_protocol as protocol
assert not {'pydantic', 'pydantic_ai', 'a13n_harness'} & sys.modules.keys()
from a13n_stream_protocol import CustomEventAssembler
from a13n_stream_protocol.fragments import CustomEventAssembler as implementation
assert CustomEventAssembler is implementation is protocol.CustomEventAssembler
CustomEventAssembler()
assert not {'pydantic_ai', 'a13n_harness', 'a13n_stream_protocol.observer'} & sys.modules.keys()
"""
    if eager:
        script = "import sys; sys.set_lazy_imports_filter(lambda *args: False)\n" + script
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_public_exports_are_cached_and_discoverable() -> None:
    import a13n_stream_protocol as protocol

    for name in protocol.__all__:
        assert getattr(protocol, name) is getattr(protocol, name)
        assert name in dir(protocol)
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = protocol.not_a_protocol_export
