from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_environment import _local_identity as identity_module


def test_backing_identity_fails_closed_when_evidence_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = {"provider_key": "local", "policy": {}}
    assert identity_module.local_backing_identity(roots=(tmp_path / "missing",), **arguments) is None
    monkeypatch.setattr(Path, "stat", lambda self, **kwargs: SimpleNamespace(st_ino=0, st_mode=0o040700))
    assert identity_module.local_backing_identity(roots=(tmp_path,), **arguments) is None


def test_backing_identity_binds_provider_host_and_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def identity(provider="first", policy=None):
        return identity_module.local_backing_identity(provider_key=provider, roots=(tmp_path,), policy=policy)

    first = identity()
    assert first is not None
    assert identity(provider="second") != first
    assert identity(policy={"read_only": True}) != first
    monkeypatch.setattr(identity_module.platform, "node", lambda: "another-host")
    assert identity() != first
    monkeypatch.setattr(identity_module.platform, "node", lambda: "")
    assert identity() is None
