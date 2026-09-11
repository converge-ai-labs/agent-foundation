from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.cli import CliRequest, cli
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.file_context import MAX_INLINE_CONTEXT_BYTES
from a13n_harness_ui.host_files import FileReadRequest
from a13n_harness_ui.webui import create_webui
from click.testing import CliRunner

from .test_app import _CompletedReconstructor, _settings, _write_configuration


@pytest.mark.anyio
@pytest.mark.parametrize("key", [None, "access-key"])
async def test_host_files_disabled_even_with_authentication_bypass(tmp_path: Path, key: str | None) -> None:
    opened = []

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(_settings(tmp_path / "state"), host_mode="webui") as app:
            opened.append(app)
            yield app

    server = create_webui(factory, api_key=key)
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        if key is not None:
            client.headers["Authorization"] = f"Bearer {key}"
        assert not (await client.get("/api/status")).json()["features"]["host_files"]
        path = str(tmp_path / "not-created")
        requests = [
            ("GET", "/api/host/files", {"params": {"path": str(tmp_path)}}),
            ("GET", "/api/host/files/metadata", {"params": {"path": path}}),
            ("GET", "/api/host/files/text", {"params": {"path": path}}),
            ("GET", "/api/host/files/content", {"params": {"path": path}}),
            ("PUT", "/api/host/files/text", {"content": "invalid body"}),
            ("PUT", "/api/host/files/content", {"params": {"path": path}, "content": b"never"}),
            ("POST", "/api/host/files/directories", {"json": {"path": path}}),
            ("POST", "/api/host/files/move", {"content": "invalid body"}),
            ("POST", "/api/host/files/delete", {"content": "invalid body"}),
            ("POST", "/api/threads/missing/host-file-captures", {"content": "invalid body"}),
        ]
        for method, url, kwargs in requests:
            response = await client.request(method, url, **kwargs)
            assert response.status_code == 403, (url, response.text)
            assert response.json()["error"]["code"] == "host_files_disabled"
        assert not Path(path).exists()
        with pytest.raises(HarnessUiError, match="share-computer"):
            await opened[0].read_host_file(FileReadRequest(path=path))


@pytest.mark.anyio
async def test_host_files_http_end_to_end_and_captured_input_survives_source_change(tmp_path: Path) -> None:
    opened = []

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"),
            configuration_path=_write_configuration(tmp_path),
            host_mode="webui",
            share_computer=True,
        ) as app:
            app._root_runs._executor._agents = _CompletedReconstructor()
            opened.append(app)
            yield app

    server = create_webui(factory, api_key="key")
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        root = tmp_path / "native"
        assert (await client.post("/api/host/files/directories", json={"path": str(root)})).status_code == 401
        client.headers["Authorization"] = "Bearer key"
        features = (await client.get("/api/status")).json()["features"]
        assert features == {
            "host_files": True,
            "host_git": opened[0].host_git_available,
            "host_terminal": False,
            "shared_drafts": False,
        }
        assert (await client.post("/api/host/files/directories", json={"path": str(root)})).status_code == 200
        source = root / "source.py"
        result = await client.put(
            "/api/host/files/text", json={"path": str(source), "text": "first\nreviewed line\nlast\n"}
        )
        assert result.status_code == 200, result.text
        revision = result.json()["revision"]
        read = await client.get("/api/host/files/text", params={"path": str(source)})
        assert read.json()["entry"]["revision"] == revision
        assert (await client.get("/api/host/files", params={"path": str(root)})).json()["entries"][0]["path"] == str(
            source
        )
        thread = await opened[0].create_thread()
        capture_url = f"/api/threads/{thread.thread_id}/host-file-captures"
        captured = await client.post(
            capture_url,
            json={
                "path": str(source),
                "expected_revision": revision,
                "start_line": 2,
                "end_line": 2,
            },
        )
        assert captured.status_code == 200, captured.text
        capture = captured.json()
        assert "reviewed line\n" in capture["prompt_text"] and "first\n" not in capture["prompt_text"]
        assert capture["attachment"]["source"]["revision"] == revision
        attachment_id = capture["attachment"]["attachment_id"]
        source.write_text("external replacement")
        stale = await client.put(
            "/api/host/files/text", json={"path": str(source), "text": "stale", "expected_revision": revision}
        )
        assert stale.status_code == 409
        assert source.read_text() == "external replacement"
        assert (
            await client.post(capture_url, json={"path": str(source), "expected_revision": revision})
        ).status_code == 409
        # No new steering attachment protocol; strict validation preserves the caller's draft.
        rejected = await client.post(
            "/api/operations/missing-receipt/steer", json={"prompt": "review", "attachment_ids": [attachment_id]}
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "request_invalid"
        submitted = await client.post(
            f"/api/threads/{thread.thread_id}/submit", json={"prompt": "review", "attachment_ids": [attachment_id]}
        )
        assert submitted.status_code == 200, submitted.text
        await opened[0].wait_root_operation(submitted.json()["receipt_id"])
        transcript = (await opened[0].get_thread_transcript(thread_id=thread.thread_id)).model_dump_json()
        assert "reviewed line" in transcript and "external replacement" not in transcript
        retained = tmp_path / "state/threads" / thread.thread_id / "attachments" / attachment_id / "content"
        assert retained.read_bytes() == b"reviewed line\n"
        download = await client.get(f"/api/threads/{thread.thread_id}/attachments/{attachment_id}")
        assert download.content == b"reviewed line\n"
        script = root / "page.html"
        html = b"<script>alert(document.origin)</script>"
        uploaded = await client.put("/api/host/files/content", params={"path": str(script)}, content=html)
        assert uploaded.status_code == 200
        downloaded = await client.get(
            "/api/host/files/content", params={"path": str(script), "expected_revision": uploaded.json()["revision"]}
        )
        assert downloaded.status_code == 200 and downloaded.content == html
        assert downloaded.headers["content-type"] == "application/octet-stream"
        assert downloaded.headers["content-disposition"].startswith("attachment;")
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in downloaded.headers["content-security-policy"]
        assert downloaded.headers["cache-control"] == "no-store"
        assert (
            await client.put("/api/host/files/content", params={"path": str(script)}, content=b"overwrite")
        ).status_code == 409
        moved_path = root / "renamed.html"
        moved = await client.post(
            "/api/host/files/move",
            json={
                "path": str(script),
                "destination": str(moved_path),
                "expected_revision": uploaded.json()["revision"],
            },
        )
        assert moved.status_code == 200, moved.text
        deleted = await client.post(
            "/api/host/files/delete", json={"path": str(moved_path), "expected_revision": moved.json()["revision"]}
        )
        assert deleted.json()["removed_entries"] == 1
        assert (await client.get("/api/host/files/metadata", params={"path": str(moved_path)})).status_code == 404
        assert (
            await client.put(
                "/api/host/files/content", params={"path": str(moved_path)}, content=b"x" * (10 * 1024 * 1024 + 1)
            )
        ).status_code == 413
        assert not moved_path.exists()
        for data in (b"\x00\xff", b"x" * (MAX_INLINE_CONTEXT_BYTES + 1)):
            source.write_bytes(data)
            current = (await client.get("/api/host/files/metadata", params={"path": str(source)})).json()
            fallback = await client.post(
                capture_url, json={"path": str(source), "expected_revision": current["revision"]}
            )
            assert fallback.status_code == 200
            assert fallback.json()["prompt_text"] is None
            handle = fallback.json()["attachment"]["attachment_id"]
            assert (await client.get(f"/api/threads/{thread.thread_id}/attachments/{handle}")).content == data


def test_share_computer_cli_option_reaches_app_and_defaults_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness_ui.webui as webui

    observed = []

    async def run(factory, **kwargs):
        async with factory() as app:
            observed.append(app.shares_computer)

    monkeypatch.setattr(webui, "run", run)
    config = tmp_path / "settings.yaml"
    config.write_text('schema_version: "1"\n')
    base = ["--config", str(config), "--data-root", str(tmp_path / "state"), "webui", "--dangerous-skip-permissions"]
    assert CliRequest().share_computer is True
    for flags in ([], ["--share-computer"], ["--no-share-computer"]):
        result = CliRunner().invoke(cli, base + flags)
        assert result.exit_code == 0, result.output
    assert observed == [True, True, False]
    help_text = CliRunner().invoke(cli, ["webui", "--help"]).output
    assert "--share-computer" in help_text and "--no-share-computer" in help_text


def test_nonwebui_cli_does_not_inherit_sharing_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness_ui.cli_runtime as runtime

    observed = []

    async def management(app, *args, **kwargs):
        observed.append(app.shares_computer)
        return 0

    monkeypatch.setattr(runtime, "_run_management", management)
    config = tmp_path / "settings.yaml"
    config.write_text('schema_version: "1"\n')
    result = CliRunner().invoke(
        cli, ["--config", str(config), "--data-root", str(tmp_path / "state"), "config", "validate"]
    )
    assert result.exit_code == 0, result.output
    assert observed == [False]


@pytest.mark.anyio
async def test_native_files_ignore_selected_e2b_and_support_all_project_roots(tmp_path: Path) -> None:
    from a13n_harness_ui.app import HarnessUiIntegrations
    from a13n_harness_ui.extensions.environment_adapters import EnvironmentProjectAdapter

    class SelectionOnlyE2BAdapter(EnvironmentProjectAdapter):
        key = "test.e2b-project"
        provider_key = "a13n.e2b"

        def validate_profile(self, *, provider_schema_version, provider_configuration, adapter_configuration, provider):
            provider.validate_configuration(schema_version=provider_schema_version, value=provider_configuration)
            return dict(provider_configuration), dict(adapter_configuration)

        async def bind(self, **kwargs):
            raise AssertionError("Human Host Files must not open the Agent's E2B Environment")

    configuration = _write_configuration(tmp_path)
    configuration.write_text(configuration.read_text() + "  environment_profile: environment-remote\n")
    extensions = tmp_path / "extensions"
    extensions.mkdir()
    (extensions / "remote.yaml").write_text("""schema_version: "1"
kind: environment_profile
id: environment-remote
name: E2B selection
provider_key: a13n.e2b
provider_schema_version: "1"
provider_configuration: {}
adapter_key: test.e2b-project
adapter_configuration: {}
""")
    second = tmp_path / "second-root"
    second.mkdir()
    project = tmp_path / "projects/main.yaml"
    project.write_text(project.read_text() + f"  - path: {second}\n")
    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=configuration,
        host_mode="webui",
        share_computer=True,
        integrations=HarnessUiIntegrations(environment_adapters=(SelectionOnlyE2BAdapter(),)),
    ) as app:
        thread = await app.create_thread()
        assert thread.configuration.environment_profile_id == "environment-remote"
        projects = await app.projects()
        assert len(projects[0].roots) == 2
        for path in (tmp_path / "workspace", second, tmp_path / "outside-project"):
            path.mkdir(exist_ok=True)
            native = path / "native.txt"
            await app.upload_host_file(str(native), b"native host")
            assert (await app.read_host_file(FileReadRequest(path=str(native)))).text == "native host"
        # Native edits do not bypass the owning configuration generation validator.
        accepted = (await app.status()).accepted_generation_digest
        observed = await app.host_file_metadata(str(configuration))
        await app.upload_host_file(str(configuration), b"malformed: [", expected_revision=observed.revision)
        await app.reload_configuration()
        status = await app.status()
        assert status.accepted_generation_digest == accepted
        assert status.candidate_error_code is not None


@pytest.mark.anyio
async def test_network_disconnect_during_upload_and_started_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    from threading import Event as ThreadEvent

    from anyio import Event, create_task_group, fail_after, sleep_forever, to_thread

    from .test_webui import _network_server

    files = tmp_path / "native"
    files.mkdir()
    partial = files / "partial"
    committed = files / "committed"
    entered, release, completed = ThreadEvent(), ThreadEvent(), ThreadEvent()
    real_fsync, real_link = os.fsync, os.link

    def paused_fsync(fd):
        entered.set()
        assert release.wait(5)
        real_fsync(fd)

    def observe_link(*args, **kwargs):
        result = real_link(*args, **kwargs)
        completed.set()
        return result

    server = create_webui(
        lambda: open_harness_ui_app(_settings(tmp_path / "state"), host_mode="webui", share_computer=True),
        api_key="key",
    )
    async with (
        _network_server(server) as url,
        httpx.AsyncClient(
            base_url=url,
            trust_env=False,
            headers={"Authorization": "Bearer key"},
        ) as client,
    ):
        yielded = Event()

        async def incomplete_body():
            yield b"partial"
            yielded.set()
            await sleep_forever()

        async with create_task_group() as tasks:

            async def partial_upload():
                await client.put("/api/host/files/content", params={"path": str(partial)}, content=incomplete_body())

            tasks.start_soon(partial_upload)
            with fail_after(5):
                await yielded.wait()
            tasks.cancel_scope.cancel()
        assert not partial.exists()
        monkeypatch.setattr(os, "fsync", paused_fsync)
        monkeypatch.setattr(os, "link", observe_link)
        try:
            async with create_task_group() as tasks:

                async def save():
                    await client.put("/api/host/files/content", params={"path": str(committed)}, content=b"whole body")

                tasks.start_soon(save)
                assert await to_thread.run_sync(entered.wait, 5)
                tasks.cancel_scope.cancel()
        finally:
            release.set()
        assert await to_thread.run_sync(completed.wait, 5)
        # Reconcile after lost acknowledgment; never automatically retry the mutation.
        response = await client.get("/api/host/files/text", params={"path": str(committed)})
        assert response.status_code == 200 and response.json()["text"] == "whole body"
