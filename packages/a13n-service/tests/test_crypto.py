import base64
from dataclasses import replace
from pathlib import Path

import pytest
from a13n_service.infra.crypto import FILE_KEY_ID, KeyRing, SecretLocation, file_key
from a13n_service.infra.errors import ServiceError
from pydantic import SecretStr


def test_credentials_are_randomized_bound_and_survive_key_rotation():
    old_key = SecretStr(base64.b64encode(bytes(range(32))).decode())
    new_key = SecretStr(base64.b64encode(bytes(reversed(range(32)))).decode())
    old = KeyRing(active_key_id="old", keys={"old": old_key})
    location = SecretLocation("org_one", "model_providers", "credential", "mp_one")
    envelope = old.protect(b"private credential", location)
    assert old.protect(b"private credential", location) != envelope
    rotated = KeyRing(active_key_id="new", keys={"old": old_key, "new": new_key})
    assert rotated.reveal(envelope, location) == b"private credential"
    assert rotated.protect(b"private credential", location).key_id == "new"
    for field in ("organization_id", "table", "column", "row_id"):
        with pytest.raises(ServiceError, match="cannot be decrypted"):
            rotated.reveal(envelope, replace(location, **{field: "different"}))
    with pytest.raises(ServiceError, match="cannot be decrypted"):
        KeyRing(active_key_id="new", keys={"new": new_key}).reveal(envelope, location)
    with pytest.raises(ServiceError, match="not configured"):
        KeyRing(active_key_id=None, keys={}).protect(b"secret", location)


def test_a_key_file_is_generated_once_then_read(tmp_path: Path) -> None:
    path = tmp_path / "encryption.key"
    key = file_key(path)
    assert len(base64.b64decode(key.get_secret_value(), validate=True)) == 32
    assert path.stat().st_mode & 0o777 == 0o600 and [entry.name for entry in tmp_path.iterdir()] == [path.name]
    location = SecretLocation("org_one", "model_providers", "credential", "mprov_one")
    envelope = KeyRing(active_key_id=FILE_KEY_ID, keys={FILE_KEY_ID: key}).protect(b"private", location)
    again = KeyRing(active_key_id=FILE_KEY_ID, keys={FILE_KEY_ID: file_key(path)})
    assert again.reveal(envelope, location) == b"private"
    # An operator may provide the file; its key is read as it is.
    provided = tmp_path / "provided.key"
    provided.write_text(base64.b64encode(bytes(range(32))).decode() + "\n")
    assert file_key(provided).get_secret_value() == base64.b64encode(bytes(range(32))).decode()
