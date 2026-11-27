"""Regression tests for Windows host adapters that do not require a desktop."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from a13n_harness_ui import acp_sessions, browser_setup, content_plugins, host_files, memory_diff
from a13n_harness_ui.configuration.models import AcpAgentResource
from a13n_harness_ui.model_accounts import _common

pytestmark = pytest.mark.anyio


def test_windows_account_write_without_unix_permissions(tmp_path, monkeypatch):
    windows_os = SimpleNamespace(name="nt", fdopen=os.fdopen, fsync=os.fsync, replace=os.replace)
    monkeypatch.setattr(_common, "os", windows_os)
    destination = tmp_path / "credentials.json"
    _common._write_if_unchanged(destination, b'{"example": true}\n', None)
    assert destination.read_bytes() == b'{"example": true}\n'
    assert not list(tmp_path.glob("*.tmp"))


def test_account_write_closes_descriptor_before_cleanup_on_fchmod_failure(tmp_path, monkeypatch):
    descriptor = None

    def fail(fd, mode):
        nonlocal descriptor
        descriptor = fd
        assert mode == 0o600
        raise OSError("cannot apply mode")

    local_os = SimpleNamespace(name="posix", fdopen=os.fdopen, fchmod=fail)
    monkeypatch.setattr(_common, "os", local_os)
    with pytest.raises(OSError, match="cannot apply mode"):
        _common._write_if_unchanged(tmp_path / "credentials.json", b"{}", None)
    assert descriptor is not None
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("command", ["agent", "./bin/agent"])
def test_local_acp_resolves_native_shim_using_launch_path(tmp_path, monkeypatch, command):
    workspace = tmp_path / "workspace"
    search_dir = tmp_path / "path-bin"
    shim = (workspace / "bin" if command.startswith("./") else search_dir) / "agent.cmd"
    which = Mock(return_value=str(shim))
    monkeypatch.setattr(acp_sessions.shutil, "which", which)
    resource = AcpAgentResource(
        schema_version="1", kind="acp_agent", id="acp-windows", name="Test", command=command, args=("acp",)
    )
    transport = acp_sessions.local_transport(resource, str(workspace), environ={"PATH": str(search_dir)})
    which.assert_called_once_with(
        str(workspace / command) if command.startswith("./") else command, path=str(search_dir)
    )
    assert transport.executable == str(shim)
    assert transport.arguments == ("acp",)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PATHEXT behavior")
def test_local_acp_finds_real_windows_shim(tmp_path):
    (tmp_path / "agent.cmd").write_bytes(b"@echo off\r\n")
    resource = AcpAgentResource(schema_version="1", kind="acp_agent", id="acp-windows", name="Test", command="agent")
    transport = acp_sessions.local_transport(resource, str(tmp_path), environ={"PATH": str(tmp_path)})
    assert Path(transport.executable) == tmp_path / "agent.cmd"


def test_memory_diff_writes_utf8_lf_candidates(monkeypatch):
    real_write = Path.write_text
    written = []

    def record(path, data, **options):
        assert options == {"encoding": "utf-8", "newline": "\n"}
        result = real_write(path, data, **options)
        written.append(path.read_bytes())
        return result

    monkeypatch.setattr(Path, "write_text", record)
    before = "茶\n"
    diff = memory_diff.diff_context(
        {"note.md": before}, {"note.md": hashlib.sha256(before.encode()).hexdigest()[:16]}, {"note.md": "咖啡\n"}
    )
    assert len(written) == 2
    assert "茶\n".encode() in written[0] and "咖啡\n".encode() in written[1]
    assert all(b"\r" not in body for body in written)
    assert "咖啡" in diff


def test_content_plugin_git_decodes_utf8_and_replaces_invalid_bytes(monkeypatch):
    run = subprocess.run

    def git_with_non_ascii_output(_argv, **options):
        return run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xe8\\x8c\\xb6\\xff')"], **options)

    monkeypatch.setattr(content_plugins.subprocess, "run", git_with_non_ascii_output)
    assert content_plugins._run_git(("rev-parse",), capture=True) == "茶\ufffd"


@pytest.mark.parametrize("platform", ["windows", "other"])
async def test_browser_scan_skips_unsupported_hosts(monkeypatch, platform):
    candidates = Mock(side_effect=AssertionError("unsupported host must not enumerate browsers"))
    monkeypatch.setattr(browser_setup, "host_platform", lambda: platform)
    monkeypatch.setattr(browser_setup, "system_browsers", candidates)
    assert await browser_setup.scan_browsers() == browser_setup.BrowserScan(platform=platform)
    candidates.assert_not_called()


async def test_windows_host_file_overwrite_does_not_chmod_temporary_file(tmp_path, monkeypatch):
    # POSIX stat results do not necessarily expose the Windows birth time.
    monkeypatch.setattr(host_files, "_revision", lambda _info: "revision-test")
    windows_os = Mock(wraps=os)
    windows_os.name = "nt"
    monkeypatch.setattr(host_files, "os", windows_os)
    path = tmp_path / "document"
    path.write_bytes(b"original")
    files = host_files.HostFiles(enabled=True)
    revision = (await files.metadata(str(path))).revision

    await files.write(str(path), b"replacement", expected_revision=revision)

    windows_os.chmod.assert_not_called()
    assert path.read_bytes() == b"replacement"
    assert not list(tmp_path.glob(".a13n-write-*"))
