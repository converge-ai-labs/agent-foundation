"""Execute the shipped foreground runner; no canned command-result dictionaries."""

import asyncio
import json
import os
import signal
import sys
from importlib.resources import files

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Fixture executes POSIX guest helpers on the Host")


@pytest.mark.parametrize("case", ["bounded-output", "timeout", "descendant"])
def test_guest_runner_foreground_bounds(case, tmp_path):
    async def scenario():
        source = files("a13n_harness.providers.environment").joinpath("_guest", "command.py").read_text()
        if case == "bounded-output":
            command = [sys.executable, "-c", "import os; os.write(1,b'a'*200000); os.write(2,b'b'*200000)"]
        elif case == "timeout":
            command = [sys.executable, "-c", "import time; time.sleep(30)"]
        else:
            # The parent exits; its child retains both pipes. The helper must kill the process group.
            command = [
                sys.executable,
                "-c",
                "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); open('child','w').write(str(p.pid))",
            ]
        request = {"argv": command, "cwd": str(tmp_path), "env": {}, "unset": [], "timeout": 0.5, "limit": 128}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            source,
            json.dumps(request),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
            assert process.returncode == 0, stderr.decode()
            result = json.loads(stdout)
            assert result["cleanup"] == "complete"
            if case == "bounded-output":
                import base64

                assert result["exit_code"] == 0 and not result["timed_out"]
                assert result["produced"] == [200000, 200000]
                assert base64.b64decode(result["stdout"]) == b"a" * 128
                assert base64.b64decode(result["stderr"]) == b"b" * 128
            else:
                assert result["timed_out"]
                if case == "timeout":
                    assert result["exit_code"] == -signal.SIGKILL
                else:
                    # A killed child may remain a zombie until its new parent reaps it.
                    child = int((tmp_path / "child").read_text())
                    check = await asyncio.create_subprocess_exec(
                        "ps", "-o", "stat=", "-p", str(child), stdout=asyncio.subprocess.PIPE
                    )
                    state, _ = await check.communicate()
                    assert not state.strip() or state.strip().startswith(b"Z")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if (tmp_path / "child").exists():
                try:
                    os.kill(int((tmp_path / "child").read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    asyncio.run(scenario())
