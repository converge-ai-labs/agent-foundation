"""Isolated real App for Node protocol tests; no browser or external provider."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.webui import create_webui
from pydantic_ai.models.function import FunctionModel


async def main() -> None:
    with TemporaryDirectory(prefix="a13n-webui-protocol-") as directory:
        root = Path(directory)
        os.environ["HOME"] = directory
        os.environ["USERPROFILE"] = directory
        configuration = root / "config" / "a13n-harness-ui.yaml"
        configuration.parent.mkdir()
        configuration.write_text('schema_version: "1"\ndefaults:\n  agent: agent-fixture\n', encoding="utf-8")
        sources = {
            "models/fixture.yaml": 'schema_version: "1"\nkind: model\nid: model-fixture\nname: Fixture\nroute: openai:gpt-5\nauthentication: {kind: api_key, env: FIXTURE_MODEL_KEY}\n',
            "agents/fixture.yaml": 'schema_version: "1"\nkind: agent\nid: agent-fixture\nname: Fixture\nmodel: model-fixture\n',
        }
        for name, content in sources.items():
            path = configuration.parent / name
            path.parent.mkdir()
            path.write_text(content, encoding="utf-8")

        async def model(messages, info):
            yield "Protocol "
            await asyncio.sleep(0.4)
            yield "response"

        async def resolve(self, context, model_id):
            return FunctionModel(stream_function=model)

        HarnessUiModelResolver.__call__ = resolve
        settings = HarnessUiSettings(storage=StorageSettings(data_root=root / "data"), pricing_auto_update=False)
        server = create_webui(
            lambda: open_harness_ui_app(
                settings,
                configuration_path=configuration,
                host_mode="webui",
                share_computer=False,
                instrumentation=None,
            ),
            api_key="test-only-key",
        )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        native = uvicorn.Server(uvicorn.Config(server, log_level="error", lifespan="on", ws="websockets-sansio"))
        task = asyncio.create_task(native.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not native.started:
                    if task.done():
                        await task
                        raise RuntimeError("Protocol listener stopped before startup")
                    await asyncio.sleep(0.01)
            print(json.dumps({"origin": f"http://127.0.0.1:{sock.getsockname()[1]}"}), flush=True)
            await asyncio.to_thread(sys.stdin.readline)
        finally:
            native.should_exit = True
            async with asyncio.timeout(10):
                await task
            sock.close()


if __name__ == "__main__":
    asyncio.run(main())
