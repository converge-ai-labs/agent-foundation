from __future__ import annotations

import base64
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from a13n_harness.model_auth import CodexCredentials, GrokCredentials
from a13n_ui.model_accounts import (
    AccountProjection,
    AccountStoreConflictError,
    AccountStoreError,
    Availability,
    CodexAccountStore,
    ExpiryStatus,
    GrokAccountStore,
    Provider,
    RequiredAction,
    StoreKind,
    resolve_codex_policy,
    resolve_grok_policy,
)
from a13n_ui.model_accounts import codex as codex_module

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
_GROK_ISSUER = "https://issuer.example"
_GROK_CLIENT = "client-id"
_GROK_SCOPE = f"{_GROK_ISSUER}::{_GROK_CLIENT}"


def _jwt(*, expires_at: datetime, subject: str = "account-1", account_id: str | None = None) -> str:
    header = {"alg": "none", "typ": "JWT"}
    claims: dict[str, Any] = {"exp": int(expires_at.timestamp()), "sub": subject}
    if account_id is not None:
        claims["https://api.openai.com/auth"] = {"chatgpt_account_id": account_id}

    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode(header)}.{encode(claims)}.signature"


def _codex_tokens(*, account_id: str, expires_at: datetime, marker: str) -> dict[str, Any]:
    return {
        "id_token": _jwt(expires_at=expires_at + timedelta(hours=1), account_id=account_id),
        "access_token": _jwt(expires_at=expires_at, subject=f"access-{marker}"),
        "refresh_token": f"refresh-{marker}",
        "account_id": account_id,
    }


def _codex_credential(*, account_id: str, expires_at: datetime, marker: str) -> CodexCredentials:
    tokens = _codex_tokens(account_id=account_id, expires_at=expires_at, marker=marker)
    return CodexCredentials(
        account_id=account_id,
        expires_at=expires_at,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        id_token=tokens["id_token"],
    )


def _grok_entry(*, account_id: str, expires_at: datetime, marker: str) -> dict[str, Any]:
    return {
        "key": f"grok-access-{marker}",
        "auth_mode": "oidc",
        "create_time": (_NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "user_id": account_id,
        "email": "fixture@example.test",
        "coding_data_retention_opt_out": True,
        "team_blocked_reasons": [],
        "refresh_token": f"grok-refresh-{marker}",
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "oidc_issuer": _GROK_ISSUER,
        "oidc_client_id": _GROK_CLIENT,
    }


def _grok_credential(*, account_id: str, expires_at: datetime, marker: str) -> GrokCredentials:
    return GrokCredentials(
        account_id=account_id,
        auth_mode="oidc",
        create_time=_NOW,
        expires_at=expires_at,
        issuer=_GROK_ISSUER,
        client_id=_GROK_CLIENT,
        access_token=f"grok-access-{marker}",
        refresh_token=f"grok-refresh-{marker}",
    )


async def test_codex_policy_resolves_upstream_home_and_store_mode(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')

    selected = await resolve_codex_policy(environ={"CODEX_HOME": str(codex_home)})

    assert selected.kind is StoreKind.KEYRING
    assert selected.path is None
    assert selected.supported is False
    assert selected.required_action is RequiredAction.SWITCH_TO_FILE


async def test_codex_reuses_upstream_store_and_projects_no_tokens(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    expires_at = _NOW + timedelta(hours=1)
    secret = _codex_tokens(account_id="account-1", expires_at=expires_at, marker="initial")
    (codex_home / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": secret,
                "last_refresh": (_NOW - timedelta(days=1)).isoformat(),
                "unrelated": {"kept": True},
            }
        )
    )
    store = CodexAccountStore(await resolve_codex_policy(environ={"CODEX_HOME": str(codex_home)}))

    projection = await store.inspect(now=_NOW)
    credentials = await store.load()

    assert projection.availability is Availability.AVAILABLE
    assert projection.expiry is ExpiryStatus.VALID
    assert projection.usable is True
    assert credentials.access_token == secret["access_token"]
    assert secret["access_token"] not in repr(credentials)
    assert secret["refresh_token"] not in repr(credentials)
    assert secret["access_token"] not in repr(projection)


async def test_codex_login_returns_only_safe_account_projection(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    store = CodexAccountStore(await resolve_codex_policy(environ={"CODEX_HOME": str(codex_home)}))
    credentials = _codex_credential(
        account_id="account-1",
        expires_at=_NOW + timedelta(hours=2),
        marker="login-secret",
    )

    async def login(_request: object) -> CodexCredentials:
        return credentials

    projection = await store.login(login, now=_NOW)

    assert isinstance(projection, AccountProjection)
    assert projection.availability is Availability.AVAILABLE
    assert projection.usable is True
    assert "codex-access-login-secret" not in repr(projection)
    assert "codex-refresh-login-secret" not in repr(projection)


async def test_codex_save_preserves_document_and_existing_id_token(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    initial = _codex_tokens(account_id="account-1", expires_at=_NOW + timedelta(minutes=1), marker="old")
    initial["upstream_token_metadata"] = {"keep": "token-field"}
    auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": initial,
                "agent_identity": {"unrelated": "record"},
                "custom_extension": [1, 2, 3],
            }
        )
    )
    store = CodexAccountStore(await resolve_codex_policy(environ={"CODEX_HOME": str(codex_home)}))
    refreshed = _codex_credential(
        account_id="account-1",
        expires_at=_NOW + timedelta(hours=2),
        marker="rotated",
    )
    refreshed_without_id_token = CodexCredentials(
        account_id=refreshed.account_id,
        expires_at=refreshed.expires_at,
        access_token=refreshed.access_token,
        refresh_token=refreshed.refresh_token,
    )

    await store.load()
    await store.save(refreshed_without_id_token)
    persisted = json.loads(auth_path.read_text())

    assert persisted["custom_extension"] == [1, 2, 3]
    assert persisted["agent_identity"] == {"unrelated": "record"}
    assert persisted["tokens"]["upstream_token_metadata"] == {"keep": "token-field"}
    assert persisted["tokens"]["refresh_token"] == refreshed.refresh_token
    assert persisted["tokens"]["id_token"] == initial["id_token"]
    if os.name != "nt":
        assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600


async def test_codex_save_rejects_account_switch(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    original = {
        "auth_mode": "chatgpt",
        "tokens": _codex_tokens(
            account_id="account-1",
            expires_at=_NOW + timedelta(minutes=1),
            marker="old",
        ),
    }
    auth_path.write_text(json.dumps(original))
    store = CodexAccountStore(await resolve_codex_policy(environ={"CODEX_HOME": str(codex_home)}))

    await store.load()
    with pytest.raises(AccountStoreConflictError) as conflict:
        await store.save(
            _codex_credential(
                account_id="account-2",
                expires_at=_NOW + timedelta(hours=2),
                marker="replacement",
            )
        )

    assert conflict.value.code == "account_identity_changed"
    assert json.loads(auth_path.read_text()) == original


async def test_codex_atomic_cas_never_overwrites_a_late_sibling_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    original = {
        "auth_mode": "chatgpt",
        "tokens": _codex_tokens(
            account_id="account-1",
            expires_at=_NOW + timedelta(minutes=1),
            marker="old",
        ),
    }
    auth_path.write_text(json.dumps(original))
    store = CodexAccountStore(await resolve_codex_policy(environ={"CODEX_HOME": str(codex_home)}))
    actual_write = codex_module.write_json_if_unchanged
    sibling = {
        "auth_mode": "chatgpt",
        "tokens": _codex_tokens(
            account_id="account-2",
            expires_at=_NOW + timedelta(hours=4),
            marker="other-account",
        ),
        "published_by": "sibling",
    }

    async def race_before_cas(*args: object, **kwargs: object) -> None:
        auth_path.write_text(json.dumps(sibling))
        await actual_write(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(codex_module, "write_json_if_unchanged", race_before_cas)

    await store.load()
    with pytest.raises(AccountStoreConflictError) as conflict:
        await store.save(
            _codex_credential(
                account_id="account-1",
                expires_at=_NOW + timedelta(hours=2),
                marker="callback",
            )
        )

    assert conflict.value.code == "account_store_conflict"
    assert json.loads(auth_path.read_text()) == sibling


async def test_codex_invalid_schema_does_not_run_login_or_overwrite(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth_path = codex_home / "auth.json"
    original = b'{"auth_mode":"chatgpt","tokens":{"access_token":7}}\n'
    auth_path.write_bytes(original)
    store = CodexAccountStore(await resolve_codex_policy(environ={"CODEX_HOME": str(codex_home)}))

    async def should_not_login(_request: object) -> CodexCredentials:
        raise AssertionError("login callback must not run")

    with pytest.raises(AccountStoreError) as invalid:
        await store.login(should_not_login)
    assert invalid.value.code == "account_store_incompatible"
    assert auth_path.read_bytes() == original


async def test_grok_policy_honors_path_precedence_and_rejects_process_store(tmp_path: Path) -> None:
    custom = tmp_path / "shared" / "auth.json"
    policy = resolve_grok_policy(
        environ={
            "GROK_HOME": str(tmp_path / "ignored-home"),
            "GROK_AUTH_PATH": str(custom),
        }
    )
    assert policy.kind is StoreKind.FILE
    assert policy.path == custom

    process_policy = resolve_grok_policy(
        environ={
            "GROK_AUTH_PATH": str(custom),
            "GROK_AUTH": json.dumps(
                {
                    "key": "process-secret",
                    "auth_mode": "oidc",
                    "create_time": _NOW.isoformat(),
                    "user_id": "account-1",
                }
            ),
        }
    )
    projection = await GrokAccountStore(process_policy, scope=_GROK_SCOPE).inspect(now=_NOW)
    assert process_policy.kind is StoreKind.PROCESS
    assert process_policy.writable is False
    assert projection.availability is Availability.UNSUPPORTED
    assert "process-secret" not in repr(process_policy)
    assert "process-secret" not in repr(projection)

    with pytest.raises(AccountStoreError) as malformed_inline:
        resolve_grok_policy(environ={"GROK_AUTH_PATH": str(custom), "GROK_AUTH": "not-json"})
    assert malformed_inline.value.code == "account_policy_invalid"


async def test_grok_save_preserves_other_scopes_and_entry_fields(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    initial = _grok_entry(
        account_id="grok-user-1",
        expires_at=_NOW + timedelta(minutes=1),
        marker="old",
    )
    initial["future_supported_field"] = {"preserve": True}
    other_scope = "https://other.example::other-client"
    other_entry = {"provider_owned": "untouched", "key": "other-secret"}
    auth_path.write_text(json.dumps({_GROK_SCOPE: initial, other_scope: other_entry}))
    store = GrokAccountStore(
        resolve_grok_policy(environ={"GROK_AUTH_PATH": str(auth_path)}),
        scope=_GROK_SCOPE,
    )
    refreshed = _grok_credential(
        account_id="grok-user-1",
        expires_at=_NOW + timedelta(hours=2),
        marker="rotated",
    )

    loaded = await store.load()
    assert "grok-refresh-old" not in repr(loaded)
    await store.save(refreshed)
    persisted = json.loads(auth_path.read_text())

    assert persisted[other_scope] == other_entry
    assert persisted[_GROK_SCOPE]["future_supported_field"] == {"preserve": True}
    assert persisted[_GROK_SCOPE]["key"] == refreshed.access_token
    assert persisted[_GROK_SCOPE]["refresh_token"] == refreshed.refresh_token
    if os.name != "nt":
        assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600


async def test_grok_login_rejects_account_switch(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                _GROK_SCOPE: _grok_entry(
                    account_id="grok-user-1",
                    expires_at=_NOW + timedelta(minutes=1),
                    marker="old",
                )
            }
        )
    )
    store = GrokAccountStore(
        resolve_grok_policy(environ={"GROK_AUTH_PATH": str(auth_path)}),
        scope=_GROK_SCOPE,
    )
    before = auth_path.read_bytes()

    async def switch_account(_request: object) -> GrokCredentials:
        return _grok_credential(
            account_id="grok-user-2",
            expires_at=_NOW + timedelta(hours=2),
            marker="replacement",
        )

    with pytest.raises(AccountStoreError) as confirmation:
        await store.login(switch_account)
    assert confirmation.value.code == "account_switch_confirmation_required"
    assert auth_path.read_bytes() == before


async def test_grok_unknown_active_schema_fails_without_touching_other_scopes(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    original = json.dumps(
        {
            _GROK_SCOPE: {
                "key": "secret",
                "auth_mode": "generic-oauth",
                "create_time": _NOW.isoformat(),
                "user_id": "user",
            },
            "another-scope": {"opaque": [1, 2, 3]},
        }
    ).encode()
    auth_path.write_bytes(original)
    store = GrokAccountStore(
        resolve_grok_policy(environ={"GROK_AUTH_PATH": str(auth_path)}),
        scope=_GROK_SCOPE,
    )

    with pytest.raises(AccountStoreError) as invalid:
        await store.inspect(now=_NOW)

    assert invalid.value.provider is Provider.GROK
    assert invalid.value.code == "account_kind_incompatible"
    assert auth_path.read_bytes() == original
