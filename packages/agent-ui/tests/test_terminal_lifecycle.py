"""Terminal lifecycle work never gains update-install authority."""

import asyncio
import json
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from a13n_ui.cli import CliRequest
from a13n_ui.interactive import lifecycle
from a13n_ui.interactive.lifecycle import check_update, resume_hint, terminal_logging, update_notice


def test_versions_use_pep440_and_ignore_preview() -> None:
    assert update_notice("1.9", "1.10")
    assert update_notice("1.9", "1.10rc1") is None
    assert update_notice("1.10", "1.9") is None
    assert update_notice("bad", "1.9") is None


@pytest.mark.anyio
async def test_daily_update_cache_requires_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache" / "terminal-update.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"latest": "2.0", "checked_at": time.time()}))

    def no_client(*args: object, **kwargs: object) -> None:
        pytest.fail("fresh cache must not contact PyPI")

    monkeypatch.setattr(httpx2, "AsyncClient", no_client)
    assert "uv tool upgrade" in (await check_update(tmp_path, current="1.0") or "")


@pytest.mark.anyio
async def test_terminal_logs_are_files_and_plugin_diagnostics_are_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notices: list[str] = []
    logger = logging.getLogger("a13n_ui.lifecycle_test")
    with terminal_logging(tmp_path, "INFO", notices.append) as path:
        for _ in range(2):
            logger.warning("content_plugin_skipped", extra={"path": "/plugins/installed", "reason": "legacy"})
        logger.info("ordinary diagnostic")
        await asyncio.sleep(0)
    assert len(notices) == 1
    assert "ordinary diagnostic" in path.read_text()
    assert "\x1b" not in path.read_text()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def _assert_resume_command(hint: str, arguments: list[str], *, platform: str = os.name) -> None:
    command = hint.splitlines()[1].removeprefix("  ")
    if platform == "nt":
        # shlex is a POSIX parser: it consumes unquoted Windows path backslashes.
        assert command == subprocess.list2cmdline(arguments)
    else:
        assert shlex.split(command) == arguments


def test_resume_hint_windows_quoting_on_every_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Replace only this module's OS view; changing os.name globally breaks pathlib.
    monkeypatch.setattr(lifecycle, "os", SimpleNamespace(name="nt", environ={}))
    request = CliRequest(config_path=tmp_path / "a b.yaml", data_root=tmp_path / "plain")
    _assert_resume_command(
        resume_hint(request, "thread_123", tmp_path),
        [
            "a13n-ui",
            "--config",
            str(request.config_path),
            "--data-root",
            str(request.data_root),
            "--resume",
            "thread_123",
        ],
        platform="nt",
    )


def test_resume_hint_keeps_explicit_roots(tmp_path: Path) -> None:
    request = CliRequest(config_path=tmp_path / "a b.yaml", data_root=tmp_path / "data files")
    hint = resume_hint(request, "thread_123", tmp_path)
    _assert_resume_command(
        hint,
        [
            "a13n-ui",
            "--config",
            str(request.config_path),
            "--data-root",
            str(request.data_root),
            "--resume",
            "thread_123",
        ],
    )


@pytest.mark.anyio
@pytest.mark.parametrize("behavior", ["success", "offline", "oversize", "invalid"])
async def test_update_checker_is_bounded_public_and_failure_tolerant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behavior: str
) -> None:
    original = httpx2.AsyncClient
    requests = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.url.host == "pypi.org"
        assert "authorization" not in request.headers
        if behavior == "offline":
            raise httpx2.ConnectError("offline")
        if behavior == "oversize":
            return httpx2.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
        if behavior == "invalid":
            return httpx2.Response(200, json={"info": {"version": "not a version"}})
        return httpx2.Response(200, json={"info": {"version": "2.0"}})

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"] == 2
        return original(transport=httpx2.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", client)
    notice = await check_update(tmp_path, current="1.0")
    assert len(requests) == 1
    assert bool(notice) == (behavior == "success")
    assert (tmp_path / "cache" / "terminal-update.json").exists() == (behavior == "success")


@pytest.mark.parametrize("directory_name", ["plain", "with spaces"])
def test_resume_hint_preserves_environment_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory_name: str
) -> None:
    data_root = tmp_path / directory_name
    monkeypatch.setenv("A13N_UI_DATA_ROOT", str(data_root))
    _assert_resume_command(
        resume_hint(CliRequest(), "thread_1", tmp_path),
        ["a13n-ui", "--data-root", str(data_root), "--resume", "thread_1"],
    )
