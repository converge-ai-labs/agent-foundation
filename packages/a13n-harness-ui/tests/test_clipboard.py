"""Clipboard diagnostics use simulated environments, never the user's clipboard."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive import attachments
from a13n_harness_ui.interactive.commands import CommandRegistry
from a13n_harness_ui.interactive.shell import CliShell
from PIL import Image
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


@pytest.fixture(autouse=True)
def isolated_clipboard(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SSH_TTY", "SSH_CONNECTION", "WAYLAND_DISPLAY", "DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    # Replace this module's reference, not the process-wide platform used by
    # prompt_toolkit to select native Windows/POSIX input backends.
    monkeypatch.setattr(attachments, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(attachments.shutil, "which", lambda name: None)

    def unavailable():
        raise OSError("backend-specific failure")

    monkeypatch.setattr(attachments.ImageGrab, "grabclipboard", unavailable)


@pytest.mark.parametrize("ssh_variable", ["SSH_TTY", "SSH_CONNECTION"])
@pytest.mark.parametrize("result", ["unavailable", "empty", "empty-files"])
def test_ssh_failure_explains_local_remote_boundary(
    monkeypatch: pytest.MonkeyPatch, ssh_variable: str, result: str
) -> None:
    monkeypatch.setenv(ssh_variable, "present")
    if result != "unavailable":
        monkeypatch.setattr(attachments.ImageGrab, "grabclipboard", lambda: [] if result == "empty-files" else None)
    with pytest.raises(ValueError) as error:
        attachments.clipboard_images()
    message = str(error.value)
    assert "SSH session" in message
    assert "remote host's clipboard" in message
    assert "not your local computer's" in message
    assert "Cmd+V on macOS" in message
    assert "/attach <remote-path>" in message
    assert "does not forward your local clipboard" in message
    assert ("unavailable" in message) == (result == "unavailable")


@pytest.mark.parametrize(
    ("display", "helper", "installed", "expected"),
    [
        (None, None, False, "No Wayland or X11 display session was detected"),
        ("WAYLAND_DISPLAY", "wl-paste", False, "needs wl-paste"),
        ("DISPLAY", "xclip", False, "needs xclip"),
        ("DISPLAY", "xclip", True, "display session is accessible"),
        ("WAYLAND_DISPLAY", "wl-paste", True, "display session is accessible"),
    ],
)
def test_linux_unavailable_diagnostics(
    monkeypatch: pytest.MonkeyPatch, display: str | None, helper: str | None, installed: bool, expected: str
) -> None:
    if display:
        monkeypatch.setenv(display, "display-session")
    monkeypatch.setattr(attachments.shutil, "which", lambda name: f"/usr/bin/{name}" if installed else None)
    with pytest.raises(ValueError, match=expected) as error:
        attachments.clipboard_images()
    assert "Text paste still works" in str(error.value)
    assert "/attach <path>" in str(error.value)
    if helper and not installed:
        assert f"needs {helper}" in str(error.value)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_non_linux_failure_does_not_recommend_linux_helpers(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    monkeypatch.setattr(attachments.sys, "platform", platform)
    with pytest.raises(ValueError) as error:
        attachments.clipboard_images()
    assert "wl-paste" not in str(error.value)
    assert "xclip" not in str(error.value)


def test_empty_local_clipboard_does_not_claim_missing_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attachments.ImageGrab, "grabclipboard", lambda: None)
    with pytest.raises(ValueError, match="No image or files") as error:
        attachments.clipboard_images()
    assert "display session" not in str(error.value)
    assert "unavailable" not in str(error.value)


@pytest.mark.parametrize("display", [None, "DISPLAY", "WAYLAND_DISPLAY"])
def test_ssh_hints_never_block_a_working_clipboard(monkeypatch: pytest.MonkeyPatch, display: str | None) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "present")
    if display:
        monkeypatch.setenv(display, "forwarded-display")
    monkeypatch.setattr(attachments.ImageGrab, "grabclipboard", lambda: Image.new("RGB", (2, 2)))
    result = attachments.clipboard_images()
    assert len(result) == 1
    assert result[0].media_type == "image/png"
    assert result[0].data.startswith(b"\x89PNG")


def test_unreadable_copied_file_is_not_a_clipboard_backend_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "present")
    monkeypatch.setattr(attachments.ImageGrab, "grabclipboard", lambda: [str(tmp_path / "missing.png")])
    with pytest.raises(ValueError, match="regular file") as error:
        attachments.clipboard_images()
    assert "SSH session" not in str(error.value)


@pytest.mark.parametrize("name", [None, "paste-image", "attach"])
def test_clipboard_help_explains_ssh_without_reading_clipboard(name: str | None) -> None:
    text = CommandRegistry().help(name)
    assert "Over SSH" in text
    assert "Cmd+V on macOS" in text
    assert "pasting a local file path does not upload" in text


@pytest.mark.anyio
async def test_ssh_clipboard_failure_preserves_text_and_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "present")
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.composer.text = "existing draft"
        shell.insert_attachments((attachments.AttachmentUpload("notes.txt", b"notes", "text/plain"),))
        original_text = shell.composer.text
        original = shell.images
        messages = []
        monkeypatch.setattr(shell, "emit", messages.append)
        try:
            await shell.acquire_images()
            assert shell.composer.text.endswith(original_text[1:])
            assert "failed" in shell.inline.display(shell.composer.text)
            assert shell.images == original
            assert len(messages) == 1
            assert "SSH session" in messages[0]
        finally:
            shell.renderer.transcript.close()
