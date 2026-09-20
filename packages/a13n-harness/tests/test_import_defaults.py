"""Import-time defaults are applied before loading Pydantic AI."""

from __future__ import annotations

import os
import subprocess
import sys

CONFIGURED_VALUES = (None, "0", "1", "")


def test_pydantic_ai_banner_default_preserves_explicit_values() -> None:
    script = """
import os
import sys

expected = sys.argv[1]
pydantic_imports = []


def check_import(event, args):
    if event == "import" and args[0] == "pydantic_ai":
        assert os.environ.get("PYDANTIC_AI_NO_BANNER") == expected
        pydantic_imports.append(args[0])


sys.addaudithook(check_import)
import a13n_harness
assert not pydantic_imports, "Provider-only imports must remain inert"
from a13n_harness import HarnessBuilder

assert pydantic_imports, "The check must observe the first Pydantic AI import"
assert os.environ["PYDANTIC_AI_NO_BANNER"] == expected
"""
    # Each interpreter pays the cold import once; start them together instead of one after another.
    processes: list[tuple[str | None, subprocess.Popen[str]]] = []
    for configured_value in CONFIGURED_VALUES:
        env = os.environ.copy()
        env.pop("PYDANTIC_AI_NO_BANNER", None)
        if configured_value is not None:
            env["PYDANTIC_AI_NO_BANNER"] = configured_value
        expected = "1" if configured_value is None else configured_value
        process = subprocess.Popen(
            [sys.executable, "-c", script, expected],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        processes.append((configured_value, process))
    for configured_value, process in processes:
        output, _ = process.communicate(timeout=60)
        assert process.returncode == 0, (configured_value, output)
