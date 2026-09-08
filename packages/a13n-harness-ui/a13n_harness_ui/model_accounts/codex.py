"""Codex-compatible file account adapter without OAuth wire assumptions."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Awaitable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from a13n_harness.model_auth import CodexCredentials
from anyio import CancelScope, Lock, to_thread
from anyio.lowlevel import checkpoint

from ._common import (
    JsonSnapshot,
    ensure_aware,
    expiry_status,
    jwt_claims,
    jwt_expiry,
    parse_timestamp,
    read_json_snapshot,
    timestamp_text,
    write_json_if_unchanged,
)
from .models import (
    AccountProjection,
    AccountStoreConflictError,
    AccountStoreError,
    Availability,
    ExpiryStatus,
    Provider,
    RequiredAction,
    StoreKind,
    StorePolicy,
)

_DEFAULT_REFRESH_WINDOW = timedelta(minutes=5)
_VALID_STORE_MODES = {
    kind.value: kind for kind in (StoreKind.FILE, StoreKind.KEYRING, StoreKind.AUTO, StoreKind.EPHEMERAL)
}


@dataclass(frozen=True, slots=True)
class CodexLoginRequest:
    replacing_shared_account: bool


class CodexLoginCallback(Protocol):
    def __call__(self, request: CodexLoginRequest) -> Awaitable[CodexCredentials]: ...


@dataclass(frozen=True, slots=True)
class _Loaded:
    snapshot: JsonSnapshot
    credential: CodexCredentials | None


def _error(message: str, code: str) -> AccountStoreError:
    return AccountStoreError(message, code=code, provider=Provider.CODEX)


def _nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(
            f"The selected Codex account has an invalid {field_name} field.",
            "account_store_incompatible",
        )
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field_name)


def _identity_from_tokens(tokens: Mapping[str, Any], id_token: str) -> str:
    account_id = _optional_string(tokens.get("account_id"), "tokens.account_id")
    if account_id is not None:
        return account_id
    claims = jwt_claims(id_token, provider=Provider.CODEX, field="id_token")
    auth_claims = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        for key in ("chatgpt_account_id", "chatgpt_user_id", "user_id"):
            value = auth_claims.get(key)
            if isinstance(value, str) and value:
                return value
    subject = claims.get("sub")
    if isinstance(subject, str) and subject:
        return subject
    raise _error(
        "The selected Codex account has no stable account identity.",
        "account_store_incompatible",
    )


def _parse_document(document: dict[str, Any]) -> CodexCredentials:
    auth_mode = document.get("auth_mode")
    if auth_mode not in (None, "chatgpt"):
        raise _error(
            "The selected Codex store does not contain subscription authentication.",
            "account_kind_incompatible",
        )
    if (
        "OPENAI_API_KEY" in document
        and document["OPENAI_API_KEY"] is not None
        and not isinstance(document["OPENAI_API_KEY"], str)
    ):
        raise _error(
            "The selected Codex account has an invalid OPENAI_API_KEY field.",
            "account_store_incompatible",
        )
    if "last_refresh" in document and document["last_refresh"] is not None:
        parse_timestamp(document["last_refresh"], provider=Provider.CODEX, field="last_refresh")
    raw_tokens = document.get("tokens")
    if not isinstance(raw_tokens, dict):
        raise _error(
            "The selected Codex store has no compatible subscription token set.",
            "account_store_incompatible",
        )
    tokens = cast(dict[str, Any], raw_tokens)
    id_token = _nonempty_string(tokens.get("id_token"), "tokens.id_token")
    access_token = _nonempty_string(tokens.get("access_token"), "tokens.access_token")
    refresh_token = _nonempty_string(tokens.get("refresh_token"), "tokens.refresh_token")
    account_id = _identity_from_tokens(tokens, id_token)
    expires_at = jwt_expiry(access_token, provider=Provider.CODEX)
    return CodexCredentials(
        account_id=account_id,
        expires_at=expires_at,
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
    )


def _validate_callback_credential(credential: object) -> CodexCredentials:
    if not isinstance(credential, CodexCredentials):
        raise _error("The Codex OAuth callback returned an invalid result.", "oauth_callback_invalid")
    if credential.expires_at.tzinfo is None:
        raise _error("The Codex OAuth callback returned an invalid result.", "oauth_callback_invalid")
    document = {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": credential.id_token,
            "access_token": credential.access_token,
            "refresh_token": credential.refresh_token,
            "account_id": credential.account_id,
        },
    }
    parsed = _parse_document(document)
    if parsed.account_id != credential.account_id or parsed.expires_at != credential.expires_at.astimezone(UTC):
        raise _error("The Codex OAuth callback returned an inconsistent result.", "oauth_callback_invalid")
    return parsed


def _newer(candidate: CodexCredentials, previous: CodexCredentials) -> bool:
    rotated = (
        candidate.access_token != previous.access_token
        or candidate.refresh_token != previous.refresh_token
        or candidate.id_token != previous.id_token
    )
    return rotated and candidate.expires_at >= previous.expires_at


def _merge_credential(document: dict[str, Any], credential: CodexCredentials, now: datetime) -> dict[str, Any]:
    merged = dict(document)
    existing_tokens = merged.get("tokens")
    tokens = dict(existing_tokens) if isinstance(existing_tokens, dict) else {}
    tokens.update(
        {
            "id_token": credential.id_token,
            "access_token": credential.access_token,
            "refresh_token": credential.refresh_token,
            "account_id": credential.account_id,
        }
    )
    merged["auth_mode"] = "chatgpt"
    merged["tokens"] = tokens
    merged["last_refresh"] = timestamp_text(now)
    return merged


def _resolve_codex_home(environ: Mapping[str, str], user_home: Path | None) -> tuple[Path, bool]:
    configured = environ.get("CODEX_HOME", "")
    if configured:
        return Path(configured).expanduser(), True
    return (user_home or Path.home()) / ".codex", False


def _check_explicit_home(path: Path) -> Path:
    try:
        if not path.is_dir():
            raise OSError
        return path.resolve(strict=True)
    except OSError:
        raise _error(
            "CODEX_HOME does not identify an available directory.",
            "account_policy_invalid",
        ) from None


def _read_codex_mode(config_path: Path) -> str:
    try:
        raw = config_path.read_bytes()
    except FileNotFoundError:
        return StoreKind.FILE.value
    except OSError:
        raise _error("The Codex credential-store policy could not be read.", "account_policy_unavailable") from None
    try:
        document = tomllib.loads(raw.decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise _error("The Codex credential-store policy is malformed.", "account_policy_invalid") from None
    mode = document.get("cli_auth_credentials_store", StoreKind.FILE.value)
    if not isinstance(mode, str) or mode not in _VALID_STORE_MODES:
        raise _error("The Codex credential-store policy is unsupported.", "account_policy_invalid")
    return mode


async def resolve_codex_policy(
    *,
    environ: Mapping[str, str] | None = None,
    user_home: Path | None = None,
    effective_store_mode: StoreKind | str | None = None,
) -> StorePolicy:
    """Resolve Codex home and its effective CLI auth storage policy.

    ``effective_store_mode`` lets an embedding pass the result of Codex's full
    managed-policy resolver. Without it, this narrow adapter reads the upstream
    user ``config.toml`` field and its upstream file default.
    """

    values = dict(os.environ if environ is None else environ)
    codex_home, explicit_home = _resolve_codex_home(values, user_home)
    if explicit_home:
        codex_home = await to_thread.run_sync(_check_explicit_home, codex_home)
    if effective_store_mode is not None:
        try:
            mode = StoreKind(effective_store_mode)
        except ValueError:
            raise _error("The Codex credential-store policy is unsupported.", "account_policy_invalid") from None
    else:
        raw_mode = await to_thread.run_sync(_read_codex_mode, codex_home / "config.toml")
        mode = _VALID_STORE_MODES[raw_mode]
    supported = mode is StoreKind.FILE
    return StorePolicy(
        provider=Provider.CODEX,
        kind=mode,
        path=codex_home / "auth.json" if mode is StoreKind.FILE else None,
        supported=supported,
        writable=supported,
        required_action=RequiredAction.NONE if supported else RequiredAction.SWITCH_TO_FILE,
    )


class CodexAccountStore:
    """Shared Codex file store with per-operation rereads and CAS writes."""

    def __init__(self, policy: StorePolicy, *, refresh_window: timedelta = _DEFAULT_REFRESH_WINDOW) -> None:
        if policy.provider is not Provider.CODEX:
            raise ValueError("CodexAccountStore requires a Codex policy")
        if refresh_window < timedelta(0):
            raise ValueError("refresh_window must not be negative")
        self.policy = policy
        self._refresh_window = refresh_window
        self._mutation_lock = Lock()
        self._save_snapshot: ContextVar[_Loaded | None] = ContextVar(f"codex-save-snapshot-{id(self)}", default=None)

    @classmethod
    async def from_environment(cls, **kwargs: Any) -> CodexAccountStore:
        return cls(await resolve_codex_policy(**kwargs))

    def _path(self) -> Path:
        if not self.policy.supported or not self.policy.writable or self.policy.path is None:
            raise _error(
                "The active Codex credential-store policy cannot be shared safely.",
                "account_store_unsupported",
            )
        return self.policy.path

    async def _load(self) -> _Loaded:
        snapshot = await read_json_snapshot(self._path(), provider=Provider.CODEX)
        credential = (
            None
            if snapshot.document is None or snapshot.document.get("tokens") is None
            else _parse_document(snapshot.document)
        )
        return _Loaded(snapshot=snapshot, credential=credential)

    async def inspect(self, *, now: datetime | None = None) -> AccountProjection:
        if not self.policy.supported:
            return AccountProjection(
                provider=Provider.CODEX,
                availability=Availability.UNSUPPORTED,
                source=self.policy.kind,
                usable=False,
                expiry=ExpiryStatus.NOT_APPLICABLE,
                required_action=self.policy.required_action,
            )
        loaded = await self._load()
        if loaded.credential is None:
            return AccountProjection(
                provider=Provider.CODEX,
                availability=Availability.ABSENT,
                source=self.policy.kind,
                usable=False,
                expiry=ExpiryStatus.NOT_APPLICABLE,
                required_action=RequiredAction.LOGIN,
            )
        expiry = expiry_status(loaded.credential.expires_at, ensure_aware(now), self._refresh_window)
        return AccountProjection(
            provider=Provider.CODEX,
            availability=Availability.AVAILABLE,
            source=self.policy.kind,
            usable=expiry is ExpiryStatus.VALID,
            expiry=expiry,
            expires_at=loaded.credential.expires_at,
            required_action=RequiredAction.NONE if expiry is ExpiryStatus.VALID else RequiredAction.REFRESH,
        )

    async def load(self) -> CodexCredentials:
        """Load the current compatible credential set for Harness model authentication."""

        loaded = await self._load()
        if loaded.credential is None:
            raise _error("A compatible Codex login is required.", "authentication_required")
        self._save_snapshot.set(loaded)
        return loaded.credential

    async def save(self, credentials: CodexCredentials) -> None:
        """Persist a Harness refresh with an optimistic pre-replace digest check."""

        if not isinstance(credentials, CodexCredentials) or credentials.expires_at.tzinfo is None:
            raise _error("The Codex OAuth callback returned an invalid result.", "oauth_callback_invalid")
        async with self._mutation_lock:
            expected = self._save_snapshot.get()
            if expected is None or expected.credential is None:
                raise _error("Codex credentials must be loaded before they are saved.", "account_store_conflict")
            if credentials.id_token is None:
                credentials = replace(credentials, id_token=expected.credential.id_token)
            refreshed = _validate_callback_credential(credentials)
            self._require_same_account(expected.credential, refreshed)
            document = _merge_credential(
                dict(expected.snapshot.document or {}),
                refreshed,
                datetime.now(UTC),
            )
            await write_json_if_unchanged(
                self._path(),
                document,
                expected.snapshot.digest,
                provider=Provider.CODEX,
            )
            self._save_snapshot.set(None)

    async def login(
        self,
        login: CodexLoginCallback,
        *,
        allow_account_switch: bool = False,
        now: datetime | None = None,
    ) -> AccountProjection:
        current_time = ensure_aware(now)
        initial = await self._load()
        callback_failed = False
        try:
            callback_result = await login(CodexLoginRequest(replacing_shared_account=initial.credential is not None))
        except Exception:
            callback_failed = True
            callback_result = None
        if callback_failed:
            raise _error("The Codex OAuth login failed.", "oauth_login_failed")
        result = _validate_callback_credential(callback_result)
        if (
            initial.credential is not None
            and initial.credential.account_id != result.account_id
            and not allow_account_switch
        ):
            raise _error(
                "Replacing the shared Codex account requires explicit confirmation.",
                "account_switch_confirmation_required",
            )

        latest = await self._load()
        if initial.credential is not None and latest.credential is not None:
            self._require_same_account(initial.credential, latest.credential)
        elif initial.credential is not None and latest.credential is None:
            raise AccountStoreConflictError(
                "The shared Codex account changed during login.",
                code="account_store_conflict",
                provider=Provider.CODEX,
            )
        if (
            latest.credential is not None
            and latest.credential.account_id == result.account_id
            and _newer(latest.credential, result)
        ):
            return await self.inspect(now=current_time)
        if latest.credential is not None and latest.credential.account_id != result.account_id:
            if initial.snapshot.digest != latest.snapshot.digest or not allow_account_switch:
                raise AccountStoreConflictError(
                    "A different shared Codex account appeared during login.",
                    code="account_store_conflict",
                    provider=Provider.CODEX,
                )
        document = _merge_credential(dict(latest.snapshot.document or {}), result, current_time)
        # Cancellation before publication prevents the write. After this boundary,
        # complete persistence and its projection before reporting the outcome.
        await checkpoint()
        projection: AccountProjection | None = None
        with CancelScope(shield=True):
            await write_json_if_unchanged(self._path(), document, latest.snapshot.digest, provider=Provider.CODEX)
            projection = await self.inspect(now=current_time)
        assert projection is not None
        return projection

    async def logout(self) -> bool:
        """Remove the shared Codex subscription token set with a CAS write."""

        loaded = await self._load()
        if loaded.credential is None:
            return False
        document = dict(loaded.snapshot.document or {})
        document.pop("tokens", None)
        document.pop("last_refresh", None)
        if document.get("auth_mode") == "chatgpt":
            document.pop("auth_mode", None)
        await write_json_if_unchanged(self._path(), document, loaded.snapshot.digest, provider=Provider.CODEX)
        return True

    @staticmethod
    def _require_same_account(expected: CodexCredentials, actual: CodexCredentials) -> None:
        if expected.account_id != actual.account_id:
            raise AccountStoreConflictError(
                "The active shared Codex account changed during the operation.",
                code="account_identity_changed",
                provider=Provider.CODEX,
            )


__all__ = [
    "CodexAccountStore",
    "CodexLoginCallback",
    "CodexLoginRequest",
    "resolve_codex_policy",
]
