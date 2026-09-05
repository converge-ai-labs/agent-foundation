"""Exercise the exact developer-facing demo, including its operator cleanup."""

import asyncio
import os
from pathlib import Path
from typing import Literal

import pytest

from a13n_environment_provider_example.cli import _parser
from a13n_environment_provider_example.remote_demo import run_demo


def test_remote_cli_exposes_connection_and_demo_paths() -> None:
    parser = _parser()
    http = parser.parse_args(
        [
            "http-envd",
            "--endpoint",
            "https://envd.example",
            "--daemon-environment-id",
            "env-a",
            "--credential-file",
            "/private/token",
        ]
    )
    assert http.endpoint == "https://envd.example"
    websocket = parser.parse_args(
        ["websocket-envd", "--daemon-environment-id", "env-a", "--credential-file", "/private/token"]
    )
    assert websocket.port == 8788
    demo = parser.parse_args(["remote-envd-demo", "--transport", "websocket", "--executable", "/bin/agent-envd"])
    assert demo.transport == "websocket"


@pytest.mark.parametrize("transport", ["http", "websocket"])
def test_local_remote_demo(transport: Literal["http", "websocket"]) -> None:
    configured = os.environ.get("AGENT_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("AGENT_ENVD_TEST_BINARY enables the real-daemon demo")
    result = asyncio.run(run_demo(Path(configured), transport))
    assert result.provider_key == f"a13n.{transport}-envd"
    assert result.text == "hello from remote envd\n"
    assert result.same_generation
    assert result.state.state == {"daemon_environment_id": "env-demo-daemon"}
