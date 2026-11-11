"""Opt-in real Chromium AT-SPI acceptance, never a CDP accessibility substitute."""

from __future__ import annotations

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from a13n_envd_client import EIPDeviceConnection, StdioTransport
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPMethodError

from .test_accessibility_linux_native import child, wait_until

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("A13N_ATSPI_TEST") != "1" or not os.environ.get("A13N_ATSPI_CHROMIUM"),
        reason="Requires the isolated AT-SPI wrapper and an explicit Chromium executable",
    ),
]


@pytest.fixture
def chromium_stderr(tmp_path):
    # Chromium can log on every native query; an unread PIPE blocks its replies.
    with (tmp_path / "chromium.log").open("wb") as log:
        yield log


@pytest.fixture
def chromium_page():
    page = Path(__file__).resolve().parents[3] / "dev/fixtures/linux-desktop/accessibility.html"
    observed = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            observed["state"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_chromium_text_capability_and_explicit_focus(tmp_path, chromium_page, chromium_stderr):
    browser = Path(os.environ["A13N_ATSPI_CHROMIUM"])
    binary = Path(os.environ["A13N_ENVD_TEST_BINARY"])
    assert browser.is_file() and binary.is_file()
    url, observed = chromium_page
    config = tmp_path / "envd.json"
    config.write_text(json.dumps({"device_id": "chromium-native", "computer_use": True}))
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "LANG", "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
    }
    environment.update(A13N_ENVD_RUNTIME_DIR=str(tmp_path / "runtime"), A13N_ENVD_STATE_DIR=str(tmp_path / "state"))
    async with (
        child(
            str(browser),
            "--no-sandbox",  # Test-only browser, disposable profile and controlled loopback page.
            "--no-first-run",
            "--no-default-browser-check",
            "--password-store=basic",
            "--no-proxy-server",
            "--disable-background-networking",
            "--disable-dev-shm-usage",
            "--ozone-platform=x11",
            "--force-renderer-accessibility",
            f"--user-data-dir={tmp_path / 'profile'}",
            f"--app={url}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=chromium_stderr,
        ) as application,
        child(
            str(binary),
            "--config",
            str(config),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        ) as daemon,
    ):
        await wait_until(lambda: "state" in observed)
        device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(daemon), expected_device_id="chromium-native", request_timeout=20
        )
        try:
            async with await device.open_session() as session:
                client = session.client
                sequence = 0

                def context():
                    nonlocal sequence
                    sequence += 1
                    return {"operation_id": f"chromium-{sequence}"}

                description = None
                async with asyncio.timeout(15):
                    while description is None or not description.ready:
                        description = await client.computer_a11y_describe(
                            eip.ComputerA11yDescribeParams.model_validate({"context": context()})
                        )
                        if not description.ready:
                            await asyncio.sleep(0.02)
                roots = None
                async with asyncio.timeout(15):
                    while roots is None or not roots.nodes:
                        roots = await client.computer_a11y_roots(
                            eip.ComputerA11yRootsParams.model_validate(
                                {
                                    "context": context(),
                                    "scope": {"kind": "application", "process_id": application.pid},
                                }
                            )
                        )
                        if not roots.nodes:
                            await asyncio.sleep(0.02)
                named = {}
                wanted = {"Focus anchor", "Browser input", "Browser textarea", "Browser contenteditable"}
                cursor = None
                # DOM publication can precede Chromium's native accessibility tree.
                # Wait for the controls, while preserving native errors as failures.
                async with asyncio.timeout(15):
                    while not wanted <= named.keys():
                        tree = await client.computer_a11y_inspect(
                            eip.ComputerA11yInspectParams.model_validate(
                                {
                                    "context": context(),
                                    "node": roots.nodes[0].reference.model_dump(),
                                    "view": "raw",
                                    "limits": {"depth": 12, "max_native_calls": 8192},
                                    "cursor": cursor.model_dump() if cursor else None,
                                }
                            )
                        )
                        named.update((node.name, node.reference) for node in tree.nodes if node.name)
                        cursor = tree.completeness.continuation
                        if cursor is None and not wanted <= named.keys():
                            assert tree.completeness.complete, tree.completeness
                            named.clear()
                            await asyncio.sleep(0.02)
                for name, element_id in (
                    ("Browser input", "input"),
                    ("Browser textarea", "textarea"),
                    ("Browser contenteditable", "editable"),
                ):
                    node = named[name]
                    for focused in (False, True):
                        target = node if focused else named["Focus anchor"]
                        focus = await client.computer_atspi_focus(
                            eip.ComputerAtspiFocusParams.model_validate(
                                {
                                    "context": context(),
                                    "node": target.model_dump(),
                                    "request": {"op": "focus"},
                                }
                            )
                        )
                        assert focus.effect == "executed" and focus.dispatch_complete
                        expected_focus = element_id if focused else "anchor"
                        await wait_until(lambda expected=expected_focus: observed["state"]["active"] == expected)
                        inspected = await client.computer_a11y_inspect(
                            eip.ComputerA11yInspectParams.model_validate(
                                {
                                    "context": context(),
                                    "node": node.model_dump(),
                                    "limits": {"depth": 0},
                                }
                            )
                        )
                        can_write = any(cap.operation == "text_contents_set" for cap in inspected.nodes[0].capabilities)
                        before = observed["state"]["values"][element_id]
                        observed_before = observed["state"]
                        desired = f"{element_id} focused={focused} 世界😀"
                        params = eip.ComputerAtspiTextContentsSetParams.model_validate(
                            {
                                "context": context(),
                                "node": node.model_dump(),
                                "request": {"op": "text_contents_set", "text": desired},
                                "readback": True,
                            }
                        )
                        if can_write:
                            result = await client.computer_atspi_text_contents_set(params)
                            assert result.effect == "executed" and result.dispatch_complete
                            assert result.native_status == "returned_success"
                            await wait_until(
                                lambda key=element_id, text=desired: observed["state"]["values"][key] == text
                            )
                        else:
                            with pytest.raises(EIPMethodError) as unsupported:
                                await client.computer_atspi_text_contents_set(params)
                            assert unsupported.value.error.data.error_type == "unsupported"
                            assert unsupported.value.error.data.dispatch_stage == "pre_dispatch"
                            # Wait for fresh independent DOM evidence, not the pre-call sample.
                            await wait_until(lambda previous=observed_before: observed["state"] is not previous)
                            # Read-only Text support never invents EditableText, focuses, or retries.
                            assert observed["state"]["active"] == expected_focus
                            assert observed["state"]["values"][element_id] == before
                        content = await client.computer_a11y_read(
                            eip.ComputerA11yReadParams.model_validate(
                                {
                                    "context": context(),
                                    "node": node.model_dump(),
                                    "request": {"kind": "text"},
                                }
                            )
                        )
                        assert content.text.status == "text"
                        assert content.text.text == (desired if can_write else before)
                        assert content.completeness.complete
        finally:
            await device.close()
