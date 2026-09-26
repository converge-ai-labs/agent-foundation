"""Shared local tracing infrastructure."""

import json
import subprocess

import pytest

from dev.observability import langfuse


def test_shared_langfuse_reuses_verified_manifest_and_preserves_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(langfuse, "_machine_directory", lambda: tmp_path)
    stack = langfuse.Langfuse()
    monkeypatch.setattr(stack, "_project_resources", lambda: ())
    compose = stack._shared_compose(initialize=True, require_compatible_source=True)
    assert compose is not None
    manifest = json.loads((tmp_path / "langfuse-v2.json").read_text())
    assert manifest["project"] == "agent-foundation-local-langfuse-v2"
    assert stack._shared_compose(initialize=False, require_compatible_source=False) == compose
    calls = []
    monkeypatch.setattr(langfuse.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(stack, "_compose", lambda path, *args: calls.append(args))
    stack.stop()
    assert calls == [("down", "--remove-orphans")]
    assert compose.exists() and (tmp_path / "langfuse-v2.json").exists()
    compose.write_text("changed")
    with pytest.raises(ValueError, match="changed unexpectedly"):
        stack._shared_compose(initialize=False, require_compatible_source=False)


def test_shared_langfuse_uses_public_fixture_configuration(monkeypatch, tmp_path):
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(langfuse.subprocess, "run", run)
    langfuse.Langfuse()._compose(tmp_path / "compose.yaml", "up", "-d")
    assert seen["env"]["LANGFUSE_LOCAL_PUBLIC_KEY"] == langfuse.PUBLIC_KEY
    assert seen["env"]["LANGFUSE_LOCAL_SECRET_KEY"] == langfuse.SECRET_KEY
    assert "--env-file" in seen["command"]
    assert seen["env"]["LANGFUSE_LOCAL_PORT"] == "3000"
