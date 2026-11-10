"""Sprites bootstrap uses stdin-carried POSIX shell without guest Python."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.sprites import commands, http
from a13n_harness.providers.environment.sprites.shared import (
    SpritesConnectionConfiguration,
    SpritesEnvironmentConfiguration,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("stdin", [None, "payload with 'quotes'\n$(touch should-not-exist)\n"])
@pytest.mark.parametrize("exit_code", [0, 7])
async def test_command_carries_quoted_argv_and_stdin_without_python(monkeypatch, tmp_path, stdin, exit_code):
    value = "secret ' ; $(touch should-not-exist)\nline"
    payload = None
    result = None
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "base64").symlink_to("/usr/bin/base64")

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def send(self, data):
            nonlocal payload, result
            if data[0] == 0:
                payload = data[1:]
            else:
                assert data == b"\x04"
                process = await asyncio.create_subprocess_exec(
                    "/bin/sh",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=tmp_path,
                    env={"PATH": str(tools)},
                )
                out, err = await process.communicate(payload)
                assert not err
                result = (out, process.returncode)

        async def __aiter__(self):
            out, code = result
            yield b"\x01" + out
            yield json.dumps({"type": "exit", "exit_code": code})

    def connect(url, **kwargs):
        assert value not in url
        assert "python" not in url
        assert parse_qs(urlsplit(url).query) == {"cmd": ["/bin/sh"], "path": ["/bin/sh"], "stdin": ["true"]}
        assert kwargs["additional_headers"]["Authorization"] == "Bearer fixture-token"
        return Socket()

    monkeypatch.setattr(commands, "connect", connect)
    ref = http.HttpReference(
        SpritesEnvironmentConfiguration(execution="envd_http", desktop=True),
        "env-test",
        None,
        NativeRuntime(SpritesConnectionConfiguration(organization="fixture"), TokenCredential(api_key="fixture-token")),
    )
    argv = [
        "/bin/sh",
        "-c",
        'printf "%s" "$1"; ' + ("/bin/cat; " if stdin is not None else "") + f"exit {exit_code}",
        "fixture",
        value,
    ]
    if exit_code:
        with pytest.raises(EnvironmentProviderError) as error:
            await commands.command(ref, argv, 5, stdin=stdin)
        assert error.value.code == "provider_command_failed"
    else:
        assert await commands.command(ref, argv, 5, stdin=stdin) == value + (stdin or "")
    assert b"python" not in payload
    assert not (tmp_path / "should-not-exist").exists()
