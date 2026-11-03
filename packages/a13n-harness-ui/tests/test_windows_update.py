"""Windows updater handoff and an offline, disposable uv-tool self-upgrade."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest
from a13n_harness_ui import _windows_update as worker
from a13n_harness_ui import updater
from a13n_harness_ui.cli import cli
from click.testing import CliRunner


@pytest.mark.parametrize("arguments", [["update"], ["--no-update-check", "update"]])
def test_windows_update_reports_handoff_not_completion(tmp_path, monkeypatch, arguments):
    command = updater.UpdateCommand("uv.exe", tmp_path)
    calls = []
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "update_command", lambda: command)
    monkeypatch.setattr(updater, "_start_windows_update", calls.append)
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    assert calls == [command]
    assert "handed off" in result.output
    assert "not complete yet" in result.output
    assert "Update complete" not in result.output


@pytest.mark.parametrize("error,code", [(OSError("launch failed"), 1), (KeyboardInterrupt(), 130)])
def test_windows_handoff_failure_does_not_run_uv_or_retry(tmp_path, monkeypatch, error, code):
    calls = []
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "update_command", lambda: updater.UpdateCommand("uv.exe", tmp_path))

    def launch(command):
        calls.append(command)
        raise error

    monkeypatch.setattr(updater, "_start_windows_update", launch)
    result = CliRunner().invoke(cli, ["update"])
    assert result.exit_code == code
    assert len(calls) == 1
    assert "handed off" not in result.output
    assert "Update complete" not in result.output


@pytest.mark.parametrize("suffix", ["", ".exe"])
def test_windows_wait_targets_only_launching_chain(tmp_path, monkeypatch, suffix):
    prefix = tmp_path / "tools" / "a13n-harness-ui"
    launcher = tmp_path / "bin" / "a13n-harness-ui.exe"
    parents = [
        SimpleNamespace(pid=10, exe=lambda: str(prefix / "Scripts" / "python.exe")),
        SimpleNamespace(pid=11, exe=lambda: str(launcher)),
        SimpleNamespace(pid=12, exe=lambda: str(tmp_path / "powershell.exe")),
    ]
    monkeypatch.setattr(updater.sys, "prefix", str(prefix))
    monkeypatch.setattr(updater.sys, "argv", [str(launcher.with_suffix(suffix)), "update"])
    monkeypatch.setattr(psutil, "Process", lambda: SimpleNamespace(parents=lambda: parents))
    assert updater._windows_update_pids() == [os.getpid(), 10, 11]


def test_windows_handoff_uses_base_python_and_explicit_handles(tmp_path, monkeypatch):
    calls = []
    events = []

    @contextmanager
    def handles(pids):
        assert pids == [10, 11]
        events.append("open")
        yield [100, 101]
        events.append("close")

    def launch(argv, **kwargs):
        assert events == ["open"]
        calls.append((argv, kwargs))

    monkeypatch.setattr(updater, "_windows_update_pids", lambda: [10, 11])
    monkeypatch.setattr(worker, "process_handles", handles)
    monkeypatch.setattr(updater.subprocess, "STARTUPINFO", SimpleNamespace, raising=False)
    monkeypatch.setattr(updater.subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    updater._start_windows_update(updater.UpdateCommand("C:/uv path/uv.exe", tmp_path))
    assert events == ["open", "close"]
    argv, kwargs = calls[0]
    assert Path(argv[0]) == Path(sys.base_prefix) / "python.exe"
    assert argv[1:4] == ("-I", "-S", "-c")
    assert argv[4] == Path(worker.__file__).read_text(encoding="utf-8")
    assert argv[5:] == ("C:/uv path/uv.exe", str(tmp_path), "100", "101")
    assert kwargs["startupinfo"].lpAttributeList == {"handle_list": [100, 101]}
    assert kwargs["creationflags"] == 16
    assert kwargs["close_fds"] is True
    assert "shell" not in kwargs


@pytest.mark.parametrize("exit_code", [0, 7, -15])
def test_worker_waits_before_running_uv_and_preserves_failure(tmp_path, monkeypatch, capsys, exit_code):
    events = []
    monkeypatch.setattr(worker, "wait_for_exit", lambda handles: events.append(("wait", handles)))

    def install(argv, **kwargs):
        assert events == [("wait", [100, 101])]
        events.append("install")
        assert argv == ("uv.exe", "tool", "upgrade", "a13n-harness-ui")
        assert kwargs == {"env": {**os.environ, "UV_TOOL_DIR": str(tmp_path)}, "check": False}
        return SimpleNamespace(returncode=exit_code)

    monkeypatch.setattr(worker.subprocess, "run", install)
    expected = exit_code if exit_code >= 0 else 1
    assert worker.run("uv.exe", str(tmp_path), [100, 101]) == expected
    assert events == [("wait", [100, 101]), "install"]
    output = capsys.readouterr()
    assert ("Update complete" in output.out) == (exit_code == 0)
    assert ("Update failed" in output.err) == (exit_code != 0)


@pytest.mark.parametrize("stage", ["wait", "install"])
@pytest.mark.parametrize(
    "error,code", [(OSError("failure"), 1), (TimeoutError("wait expired"), 1), (KeyboardInterrupt(), 130)]
)
def test_worker_failure_never_retries_or_claims_success(monkeypatch, capsys, stage, error, code):
    calls = []

    def wait(handles):
        calls.append("wait")
        if stage == "wait":
            raise error

    def install(*args, **kwargs):
        calls.append("install")
        raise error

    monkeypatch.setattr(worker, "wait_for_exit", wait)
    monkeypatch.setattr(worker.subprocess, "run", install)
    assert worker.run("uv", "tools", [100]) == code
    assert calls == (["wait"] if stage == "wait" else ["wait", "install"])
    assert "Update complete" not in capsys.readouterr().out


def test_wait_polls_for_cancellation_and_closes_handles(monkeypatch):
    results = iter([258, 0])
    closed = []
    kernel = SimpleNamespace(
        WaitForMultipleObjects=lambda count, handles, all_, millis: next(results),
        CloseHandle=closed.append,
    )
    monkeypatch.setattr(worker, "_kernel32", lambda: kernel)
    worker.wait_for_exit([100, 101])
    assert closed == [100, 101]


def test_wait_timeout_closes_handles(monkeypatch):
    closed = []
    kernel = SimpleNamespace(WaitForMultipleObjects=lambda *args: 258, CloseHandle=closed.append)
    monkeypatch.setattr(worker, "_kernel32", lambda: kernel)
    with pytest.raises(TimeoutError, match="no update was started"):
        worker.wait_for_exit([100], timeout=0)
    assert closed == [100]


@pytest.mark.parametrize("code", [0, 7, 130])
def test_result_window_waits_for_acknowledgement_without_changing_status(monkeypatch, code):
    messages = []
    monkeypatch.setattr(worker.sys, "argv", ["worker", "uv", "tools", "100"])
    monkeypatch.setattr(worker.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(worker, "run", lambda *args: code)
    monkeypatch.setattr("builtins.input", messages.append)
    assert worker.main() == code
    assert messages == ["Press Enter to close this update window."]


def _write_tool_wheel(directory: Path, version: str, source: str) -> None:
    name = "a13n_harness_ui"
    dist = f"{name}-{version}.dist-info"
    files = {
        "update_probe.py": source,
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: a13n-harness-ui\nVersion: {version}\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{dist}/entry_points.txt": "[console_scripts]\na13n-harness-ui = update_probe:main\n",
    }
    files[f"{dist}/RECORD"] = "".join(f"{path},,\n" for path in files) + f"{dist}/RECORD,,\n"
    with zipfile.ZipFile(directory / f"{name}-{version}-py3-none-any.whl", "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows process and executable locks")
def test_real_uv_self_upgrade_releases_launcher_before_entrypoint_copy(tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the offline tool installation")
    root = tmp_path / "update path with spaces"
    wheels = root / "wheels"
    wheels.mkdir(parents=True)
    log = root / "update.log"
    receipt = root / "worker.json"
    # Only dependencies come from the test environment. The command still runs
    # through a real uv-installed .exe and the production updater/worker source.
    package_root = str(Path(updater.__file__).parent.parent)
    import click

    dependencies = str(Path(click.__file__).parent.parent)
    source = f"""import sys
from importlib.metadata import version

def main():
    if sys.argv[1:] == ["--version"]:
        print(version("a13n-harness-ui"))
        return
    sys.path[:0] = {[package_root, dependencies]!r}
    import json
    import subprocess
    from pathlib import Path
    from a13n_harness_ui import updater
    popen = subprocess.Popen
    def launch(argv, **kwargs):
        assert kwargs["creationflags"] == subprocess.CREATE_NEW_CONSOLE
        with open({str(log)!r}, "wb") as output:
            child = popen(argv, **kwargs, stdin=subprocess.DEVNULL, stdout=output, stderr=output)
        Path({str(receipt)!r}).write_text(json.dumps({{"pid": child.pid, "wait_pids": updater._windows_update_pids()}}))
        return child
    subprocess.Popen = launch
    updater.update()
"""
    env = {
        **os.environ,
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "bin"),
        "UV_NO_CONFIG": "1",
        "UV_NO_INDEX": "1",
        "UV_FIND_LINKS": str(wheels),
        "UV_OFFLINE": "1",
    }
    _write_tool_wheel(wheels, "1.0.0", source)
    install = subprocess.run(
        [uv, "tool", "install", "a13n-harness-ui", "--python", sys.executable],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert install.returncode == 0, install.stderr
    _write_tool_wheel(wheels, "2.0.0", source)
    exe = root / "bin" / "a13n-harness-ui.exe"
    with subprocess.Popen(
        [str(exe), "update"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as parent:
        out, err = parent.communicate(timeout=30)
        assert parent.returncode == 0, out + err
        assert "handed off" in out
        info = json.loads(receipt.read_text())
        assert parent.pid in info["wait_pids"], info
    try:
        child = psutil.Process(info["pid"])
    except psutil.NoSuchProcess:
        child = None
    if child is not None:
        try:
            child.wait(timeout=60)
        finally:
            if child.is_running():
                child.kill()
                child.wait(timeout=10)
    output = log.read_text(encoding="utf-8")
    assert "Update complete" in output, output
    assert "os error 32" not in output
    assert "Update failed" not in output
    updated = subprocess.run([str(exe), "--version"], env=env, capture_output=True, text=True, timeout=10)
    assert updated.returncode == 0, updated.stderr
    assert updated.stdout.strip() == "2.0.0"
    assert exe.read_bytes() == (root / "tools" / "a13n-harness-ui" / "Scripts" / exe.name).read_bytes()
