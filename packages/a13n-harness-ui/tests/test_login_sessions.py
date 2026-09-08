from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_harness.model_auth import CodexCredentials, DeviceAuthorizationError
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_accounts.login import LoginRequest
from anyio import Event, fail_after, sleep

from .test_app import _settings
from .test_model_accounts import _jwt

pytestmark = pytest.mark.anyio


@pytest.fixture
def isolated_accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("GROK_AUTH_PATH", str(tmp_path / "grok" / "auth.json"))
    monkeypatch.delenv("GROK_AUTH", raising=False)
    return home


async def test_login_session_persists_only_after_success_and_confirms_account_switch(
    tmp_path: Path, isolated_accounts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = "account-first"

    async def authorize(request, method, present):
        assert method == "device"
        present(verification_url="https://example.test/device", user_code="ABC-123")
        expires = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
        return CodexCredentials(
            account_id=identity,
            expires_at=expires,
            access_token=_jwt(expires_at=expires, account_id=identity),
            refresh_token="secret-refresh",
            id_token=_jwt(expires_at=expires, account_id=identity),
        )

    monkeypatch.setattr("a13n_harness_ui.model_accounts.login.authorize_codex", authorize)
    async with open_harness_ui_app(_settings(tmp_path / "state")) as app:

        async def login(allow: bool = False):
            status = await app.start_login(LoginRequest(provider="codex", allow_account_switch=allow))
            with fail_after(3):
                while status.state in {"starting", "waiting"}:
                    await sleep(0.01)
                    status = await app.login_status(status.session_id)
            return status

        status = await login()
        assert status.state == "succeeded", status
        assert status.verification_url is None and status.user_code is None
        assert "secret-refresh" not in status.model_dump_json()
        original = (isolated_accounts / "auth.json").read_bytes()
        identity = "account-second"
        status = await login()
        assert status.error_code == "account_switch_confirmation_required"
        assert (isolated_accounts / "auth.json").read_bytes() == original
        assert (await login(True)).state == "succeeded"
        assert not (tmp_path / "state" / "auth.json").exists()


async def test_login_cancel_is_bounded_and_does_not_block_app(
    tmp_path: Path, isolated_accounts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = Event()

    async def authorize(request, method, present):
        present(verification_url="https://example.test/device", user_code="ABC-123")
        waiting.set()
        await Event().wait()

    monkeypatch.setattr("a13n_harness_ui.model_accounts.login.authorize_codex", authorize)
    async with open_harness_ui_app(_settings(tmp_path / "state")) as app:
        status = await app.start_login(LoginRequest(provider="codex"))
        await waiting.wait()
        assert (await app.status()).state.value == "ready"
        with pytest.raises(HarnessUiError, match="already active"):
            await app.start_login(LoginRequest(provider="grok"))
        with fail_after(2):
            cancelled = await app.cancel_login(status.session_id)
        assert cancelled.state == "cancelled"
        assert not (isolated_accounts / "auth.json").exists()


@pytest.mark.parametrize("reason", ["expired", "denied", "unsupported"])
async def test_login_terminal_device_errors_are_safe(
    tmp_path: Path, isolated_accounts: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    async def authorize(request, method, present):
        raise DeviceAuthorizationError("openai-codex", reason)

    monkeypatch.setattr("a13n_harness_ui.model_accounts.login.authorize_codex", authorize)
    async with open_harness_ui_app(_settings(tmp_path / "state")) as app:
        status = await app.start_login(LoginRequest(provider="codex"))
        with fail_after(3):
            while status.state in {"starting", "waiting"}:
                await sleep(0.01)
                status = await app.login_status(status.session_id)
        assert status.state == ("expired" if reason == "expired" else "failed")
        assert status.error_code == f"device_authorization_{reason}"
        assert not (isolated_accounts / "auth.json").exists()


async def test_cancel_after_publication_begins_reports_success(
    tmp_path: Path, isolated_accounts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.model_accounts import codex as codex_module
    from anyio import create_task_group

    publishing, release = Event(), Event()
    original_write = codex_module.write_json_if_unchanged

    async def write(*args, **kwargs):
        publishing.set()
        await release.wait()
        await original_write(*args, **kwargs)

    async def authorize(request, method, present):
        expires = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
        token = _jwt(expires_at=expires, account_id="account-committed")
        return CodexCredentials(
            account_id="account-committed",
            expires_at=expires,
            access_token=token,
            id_token=token,
            refresh_token="private-refresh",
        )

    monkeypatch.setattr(codex_module, "write_json_if_unchanged", write)
    monkeypatch.setattr("a13n_harness_ui.model_accounts.login.authorize_codex", authorize)
    async with open_harness_ui_app(_settings(tmp_path / "state")) as app:
        status = await app.start_login(LoginRequest(provider="codex"))
        await publishing.wait()
        outcomes = []

        async def cancel():
            outcomes.append(await app.cancel_login(status.session_id))

        async with create_task_group() as tasks:
            tasks.start_soon(cancel)
            await sleep(0.01)
            assert not outcomes
            release.set()
        assert outcomes[0].state == "succeeded"
        assert (isolated_accounts / "auth.json").exists()
