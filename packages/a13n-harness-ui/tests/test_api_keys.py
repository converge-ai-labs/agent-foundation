from __future__ import annotations

import os
from pathlib import Path

import anyio
import pytest
from a13n_harness_ui.configuration import ApiKeyAuthentication
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStore
from pydantic import SecretStr, ValidationError

pytestmark = pytest.mark.anyio


async def test_api_keys_are_private_atomic_and_reference_only(tmp_path: Path) -> None:
    store = ApiKeyStore(tmp_path / "host" / "auth.json")
    assert await store.list() == ()
    async with anyio.create_task_group() as group:
        for ref in ("key-first", "key-second"):
            group.start_soon(store.put, ApiKeyInput(credential_ref=ref, key=SecretStr("secret-" + ref)))
    assert [value.credential_ref for value in await store.list()] == ["key-first", "key-second"]
    assert "secret-" not in repr(await store.list())
    assert await store.load("key-first") == "secret-key-first"
    await store.put(ApiKeyInput(credential_ref="key-first", key=SecretStr("replacement")))
    assert await store.load("key-first") == "replacement"
    await store.delete("key-first")
    assert await store.load("key-first") is None
    assert await store.load("key-second") == "secret-key-second"
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.path.parent.stat().st_mode & 0o777 == 0o700


async def test_api_key_invalid_store_is_not_overwritten(tmp_path: Path) -> None:
    store = ApiKeyStore(tmp_path / "auth.json")
    original = '{"version":2,"keys":{"key-test":"private-material"}}'
    store.path.write_text(original)
    with pytest.raises(HarnessUiError) as caught:
        await store.put(ApiKeyInput(credential_ref="key-test", key=SecretStr("new-key")))
    assert "private-material" not in str(caught.value)
    assert store.path.read_text() == original


async def test_api_key_authentication_has_one_source() -> None:
    assert ApiKeyAuthentication(kind="api_key", env="OPENAI_API_KEY").credential_ref is None
    assert ApiKeyAuthentication(kind="api_key", credential_ref="key-openai").env is None
    for values in ({}, {"env": "OPENAI_API_KEY", "credential_ref": "key-openai"}):
        with pytest.raises(ValidationError):
            ApiKeyAuthentication(kind="api_key", **values)


async def test_api_key_store_rejects_oversized_publication_without_poisoning_reads(tmp_path: Path) -> None:
    import json

    store = ApiKeyStore(tmp_path / "auth.json")
    original = json.dumps({"version": 1, "keys": {f"key-{i}": "x" * 32768 for i in range(63)}})
    store.path.write_text(original)
    with pytest.raises(HarnessUiError):
        await store.put(ApiKeyInput(credential_ref="key-overflow", key=SecretStr("y" * 32768)))
    assert store.path.read_text() == original
    assert len(await store.list()) == 63
    await store.delete("key-0")
    assert len(await store.list()) == 62


@pytest.mark.parametrize("keys", [[], {"key-test": {"token": "private-material"}}])
async def test_api_key_malformed_known_fields_raise_safe_errors_without_overwrite(tmp_path, keys):
    import json

    store = ApiKeyStore(tmp_path / "auth.json")
    original = json.dumps({"version": 1, "keys": keys})
    store.path.write_text(original)
    for operation in (store.list, lambda: store.load("key-test")):
        with pytest.raises(HarnessUiError) as caught:
            await operation()
        assert "private-material" not in str(caught.value)
    assert store.path.read_text() == original


async def test_api_keys_and_oauth_updates_are_serialized_across_processes(tmp_path: Path) -> None:
    import json
    import sys

    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"version": 1, "keys": {}, "extension": {"retained": True}}))
    script = """
import sys
from pathlib import Path
import anyio
from pydantic import SecretStr
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStore
from a13n_harness_ui.model_accounts.chatgpt import ChatGPTAccountStore

async def main():
    path, role = Path(sys.argv[1]), sys.argv[2]
    path.with_name("ready-" + role).touch()
    with anyio.fail_after(20):
        while not path.with_name("start").exists():
            await anyio.sleep(0.01)
    for index in range(20):
        if role == "oauth":
            await ChatGPTAccountStore(path).set_blocked(f"grant-{index}", True)
        else:
            await ApiKeyStore(path).put(ApiKeyInput(
                credential_ref=f"key-{role}-{index}", key=SecretStr("synthetic-key")))

anyio.run(main)
"""
    finished = set()

    async def write(role: str) -> None:
        await anyio.run_process([sys.executable, "-c", script, str(path), role])
        finished.add(role)

    roles = ("first", "second", "oauth")
    with anyio.fail_after(30):
        async with anyio.create_task_group() as tasks:
            for role in roles:
                tasks.start_soon(write, role)
            while not all((tmp_path / ("ready-" + role)).exists() for role in roles):
                await anyio.sleep(0.01)
            (tmp_path / "start").touch()
            # Lock-free readers must always see a complete document during publication.
            while len(finished) < len(roles):
                document = json.loads(path.read_text())
                assert document["extension"] == {"retained": True}
                await anyio.sleep(0.01)
    store = ApiKeyStore(path)
    assert {key.credential_ref for key in await store.list()} == {
        f"key-{role}-{index}" for role in roles[:2] for index in range(20)
    }
    document = json.loads(path.read_text())
    assert set(document["openai_chatgpt"]["blocked_grants"]) == {f"grant-{index}" for index in range(20)}
    assert document["extension"] == {"retained": True}
