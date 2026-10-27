from __future__ import annotations

import io
import stat
import struct

import pytest
from a13n_harness_ui import browser_auth


@pytest.mark.parametrize(
    ("control", "readonly", "expected"),
    [
        ("testpass", "readonly", "87803c720bbb91d5a1dd7eb20f6980cf"),
        ("a", "b", "d8d1e367e1d17646991bb29effa94c6e"),
    ],
)
def test_password_files_match_native_tigervnc_control_and_readonly_vectors(control, readonly, expected):
    assert browser_auth.vnc_passwords(control, readonly).hex() == expected


def test_xauthority_encodes_a_local_display_cookie(monkeypatch):
    monkeypatch.setattr(browser_auth.socket, "gethostname", lambda: "test-host")
    cookie = bytes(range(16))
    stream = io.BytesIO(browser_auth.xauthority("42", cookie))
    assert struct.unpack("!H", stream.read(2))[0] == 256
    values = [stream.read(struct.unpack("!H", stream.read(2))[0]) for _ in range(4)]
    assert values == [b"test-host", b"42", b"MIT-MAGIC-COOKIE-1", cookie]
    assert not stream.read()


def test_authentication_file_replacement_is_private_and_atomic(tmp_path):
    path = tmp_path / "auth"
    path.write_bytes(b"old")
    with path.open("rb") as previous:
        browser_auth.write_private(path, b"replacement")
        assert previous.read() == b"old"
    assert path.read_bytes() == b"replacement"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_suffix(".new").exists()


def test_password_rotation_advances_integer_mtime_even_with_a_frozen_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_auth.time, "time", lambda: 1000)
    path = tmp_path / "passwords"
    for generation in range(5):
        browser_auth.write_private(path, str(generation).encode(), advance_mtime=True)
        assert int(path.stat().st_mtime) == 1000 + generation
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
