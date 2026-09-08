"""Grok Build-compatible scoped file account adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from a13n_harness.model_auth import GrokCredentials
from anyio import CancelScope, Lock
from anyio.lowlevel import checkpoint

from ._common import (
    JsonSnapshot,
    ensure_aware,
    expiry_status,
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

DEFAULT_GROK_OAUTH_ISSUER = "https://auth.x.ai"
DEFAULT_GROK_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
DEFAULT_GROK_OAUTH_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "grok-cli:access",
    "api:access",
    "conversations:read",
    "conversations:write",
    "workspaces:read",
    "workspaces:write",
)
DEFAULT_GROK_OIDC_SCOPES = ("openid", "profile", "email", "offline_access", "api:access")
DEFAULT_GROK_OAUTH_SCOPE = f"{DEFAULT_GROK_OAUTH_ISSUER}::{DEFAULT_GROK_OAUTH_CLIENT_ID}"

_DEFAULT_REFRESH_WINDOW = timedelta(minutes=5)
_LEGACY_TOKEN_TTL = timedelta(days=30)
_OAUTH_MODES = {"oidc", "external"}
_OPTIONAL_STRING_FIELDS = {
    "email",
    "first_name",
    "last_name",
    "profile_image_asset_id",
    "principal_type",
    "principal_id",
    "team_id",
    "team_name",
    "team_role",
    "organization_id",
    "organization_name",
    "organization_role",
    "user_blocked_reason",
    "refresh_token",
    "oidc_issuer",
    "oidc_client_id",
}


@dataclass(frozen=True, slots=True)
class GrokLoginRequest:
    scope: str
    replacing_shared_account: bool


class GrokLoginCallback(Protocol):
    def __call__(self, request: GrokLoginRequest) -> Awaitable[GrokCredentials]: ...


@dataclass(frozen=True, slots=True)
class _Loaded:
    snapshot: JsonSnapshot
    credential: GrokCredentials | None


def _error(message: str, code: str) -> AccountStoreError:
    return AccountStoreError(message, code=code, provider=Provider.GROK)


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(
            f"The selected Grok account has an invalid {field_name} field.",
            "account_store_incompatible",
        )
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field_name)


def _parse_entry(entry: object, scope: str) -> GrokCredentials:
    if not isinstance(entry, dict):
        raise _error("The selected Grok auth scope has an incompatible schema.", "account_store_incompatible")
    raw = cast(dict[str, Any], entry)
    access_token = _required_string(raw.get("key"), "key")
    auth_mode = _required_string(raw.get("auth_mode"), "auth_mode")
    if auth_mode not in _OAUTH_MODES:
        raise _error(
            "The selected Grok auth scope does not contain OAuth subscription authentication.",
            "account_kind_incompatible",
        )
    create_time = parse_timestamp(raw.get("create_time"), provider=Provider.GROK, field="create_time")
    account_id = _required_string(raw.get("user_id"), "user_id")
    for name in _OPTIONAL_STRING_FIELDS:
        if name in raw:
            _optional_string(raw[name], name)
    for name in ("coding_data_retention_opt_out", "has_grok_code_access"):
        if name in raw and raw[name] is not None and not isinstance(raw[name], bool):
            raise _error(
                f"The selected Grok account has an invalid {name} field.",
                "account_store_incompatible",
            )
    if "team_blocked_reasons" in raw and (
        not isinstance(raw["team_blocked_reasons"], list)
        or any(not isinstance(item, str) for item in raw["team_blocked_reasons"])
    ):
        raise _error(
            "The selected Grok account has an invalid team_blocked_reasons field.",
            "account_store_incompatible",
        )
    refresh_token = _optional_string(raw.get("refresh_token"), "refresh_token")
    issuer = _required_string(raw.get("oidc_issuer"), "oidc_issuer")
    client_id = _required_string(raw.get("oidc_client_id"), "oidc_client_id")
    if scope != f"{issuer.rstrip('/')}::{client_id}":
        raise _error(
            "The selected Grok auth entry does not match its resolved scope.",
            "account_scope_incompatible",
        )
    expires_at = (
        parse_timestamp(raw["expires_at"], provider=Provider.GROK, field="expires_at")
        if raw.get("expires_at") is not None
        else create_time + _LEGACY_TOKEN_TTL
    )
    return GrokCredentials(
        account_id=account_id,
        auth_mode=auth_mode,
        create_time=create_time,
        expires_at=expires_at,
        issuer=issuer,
        client_id=client_id,
        access_token=access_token,
        refresh_token=refresh_token,
    )


def _validate_callback_credential(credential: object, scope: str) -> GrokCredentials:
    if not isinstance(credential, GrokCredentials):
        raise _error("The Grok OAuth callback returned an invalid result.", "oauth_callback_invalid")
    if credential.create_time.tzinfo is None or credential.expires_at.tzinfo is None:
        raise _error("The Grok OAuth callback returned an invalid result.", "oauth_callback_invalid")
    entry = {
        "key": credential.access_token,
        "auth_mode": credential.auth_mode,
        "create_time": timestamp_text(credential.create_time),
        "user_id": credential.account_id,
        "refresh_token": credential.refresh_token,
        "expires_at": timestamp_text(credential.expires_at),
        "oidc_issuer": credential.issuer,
        "oidc_client_id": credential.client_id,
    }
    parsed = _parse_entry(entry, scope)
    if parsed != credential:
        raise _error("The Grok OAuth callback returned an inconsistent result.", "oauth_callback_invalid")
    return parsed


def _same_account(left: GrokCredentials, right: GrokCredentials) -> bool:
    return (
        left.account_id,
        left.issuer,
        left.client_id,
    ) == (
        right.account_id,
        right.issuer,
        right.client_id,
    )


def _newer(candidate: GrokCredentials, previous: GrokCredentials) -> bool:
    return candidate.access_token != previous.access_token and (
        candidate.create_time > previous.create_time or candidate.expires_at >= previous.expires_at
    )


def _merge_entry(existing: object, credential: GrokCredentials) -> dict[str, Any]:
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(
        {
            "key": credential.access_token,
            "auth_mode": credential.auth_mode,
            "create_time": timestamp_text(credential.create_time),
            "user_id": credential.account_id,
            "refresh_token": credential.refresh_token,
            "expires_at": timestamp_text(credential.expires_at),
            "oidc_issuer": credential.issuer,
            "oidc_client_id": credential.client_id,
        }
    )
    return merged


def _valid_inline_auth(value: str) -> bool:
    try:
        document = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return False
    return (
        isinstance(document, dict)
        and isinstance(document.get("key"), str)
        and bool(document["key"])
        and document.get("auth_mode") in _OAUTH_MODES
        and isinstance(document.get("create_time"), str)
        and isinstance(document.get("user_id"), str)
        and bool(document["user_id"])
    )


def resolve_grok_policy(
    *,
    environ: Mapping[str, str] | None = None,
    user_home: Path | None = None,
) -> StorePolicy:
    """Resolve Grok's inline-credential and auth-file precedence."""

    values = dict(os.environ if environ is None else environ)
    custom_path = values.get("GROK_AUTH_PATH")
    if custom_path == "":
        raise _error("GROK_AUTH_PATH must not be empty.", "account_policy_invalid")
    grok_home = Path(values["GROK_HOME"]) if values.get("GROK_HOME") else (user_home or Path.home()) / ".grok"
    path = Path(custom_path) if custom_path is not None else grok_home / "auth.json"
    inline = values.get("GROK_AUTH")
    if inline is not None:
        if not _valid_inline_auth(inline):
            raise _error(
                "The selected Grok process credential is malformed or incompatible.",
                "account_policy_invalid",
            )
        return StorePolicy(
            provider=Provider.GROK,
            kind=StoreKind.PROCESS,
            path=path,
            supported=False,
            writable=False,
            required_action=RequiredAction.SWITCH_TO_FILE,
        )
    return StorePolicy(
        provider=Provider.GROK,
        kind=StoreKind.FILE,
        path=path,
        supported=True,
        writable=True,
    )


async def resolve_grok_scope(policy: StorePolicy) -> str | None:
    """Resolve the only compatible OAuth scope in the selected file store."""

    if not policy.supported or policy.path is None:
        return None
    snapshot = await read_json_snapshot(
        policy.path,
        provider=Provider.GROK,
        empty_object=True,
    )
    document = snapshot.document or {}
    candidates = tuple(
        key
        for key, value in document.items()
        if "::" in key and isinstance(value, dict) and value.get("auth_mode") in _OAUTH_MODES
    )
    if len(candidates) > 1:
        raise _error(
            "The Grok account store contains multiple OAuth scopes; select one explicitly.",
            "account_scope_ambiguous",
        )
    return candidates[0] if candidates else None


class GrokAccountStore:
    """Shared scoped Grok Build store with no embedded OAuth endpoints."""

    def __init__(
        self,
        policy: StorePolicy,
        *,
        scope: str,
        refresh_window: timedelta = _DEFAULT_REFRESH_WINDOW,
    ) -> None:
        if policy.provider is not Provider.GROK:
            raise ValueError("GrokAccountStore requires a Grok policy")
        if not scope or "::" not in scope:
            raise ValueError("scope must be a resolved Grok issuer/client scope")
        if refresh_window < timedelta(0):
            raise ValueError("refresh_window must not be negative")
        self.policy = policy
        self.scope = scope
        self._refresh_window = refresh_window
        self._mutation_lock = Lock()
        self._save_snapshot: ContextVar[_Loaded | None] = ContextVar(f"grok-save-snapshot-{id(self)}", default=None)

    @classmethod
    def from_environment(cls, *, scope: str, **kwargs: Any) -> GrokAccountStore:
        return cls(resolve_grok_policy(**kwargs), scope=scope)

    def _path(self) -> Path:
        if not self.policy.supported or not self.policy.writable or self.policy.path is None:
            raise _error(
                "The active Grok credential source is process-supplied and cannot be a shared writable store.",
                "account_store_unsupported",
            )
        return self.policy.path

    async def _load(self) -> _Loaded:
        snapshot = await read_json_snapshot(self._path(), provider=Provider.GROK, empty_object=True)
        credential = None
        if snapshot.document is not None and self.scope in snapshot.document:
            credential = _parse_entry(snapshot.document[self.scope], self.scope)
        return _Loaded(snapshot=snapshot, credential=credential)

    async def inspect(self, *, now: datetime | None = None) -> AccountProjection:
        if not self.policy.supported:
            return AccountProjection(
                provider=Provider.GROK,
                availability=Availability.UNSUPPORTED,
                source=self.policy.kind,
                usable=False,
                expiry=ExpiryStatus.NOT_APPLICABLE,
                required_action=self.policy.required_action,
            )
        loaded = await self._load()
        if loaded.credential is None:
            return AccountProjection(
                provider=Provider.GROK,
                availability=Availability.ABSENT,
                source=self.policy.kind,
                usable=False,
                expiry=ExpiryStatus.NOT_APPLICABLE,
                required_action=RequiredAction.LOGIN,
            )
        expiry = expiry_status(loaded.credential.expires_at, ensure_aware(now), self._refresh_window)
        return AccountProjection(
            provider=Provider.GROK,
            availability=Availability.AVAILABLE,
            source=self.policy.kind,
            usable=expiry is ExpiryStatus.VALID,
            expiry=expiry,
            expires_at=loaded.credential.expires_at,
            required_action=RequiredAction.NONE if expiry is ExpiryStatus.VALID else RequiredAction.REFRESH,
        )

    async def load(self) -> GrokCredentials:
        """Load the current compatible scope for Harness model authentication."""

        loaded = await self._load()
        if loaded.credential is None:
            raise _error("A compatible Grok login is required.", "authentication_required")
        self._save_snapshot.set(loaded)
        return loaded.credential

    async def save(self, credentials: GrokCredentials) -> None:
        """Persist a Harness refresh with an optimistic pre-replace digest check."""

        refreshed = _validate_callback_credential(credentials, self.scope)
        async with self._mutation_lock:
            expected = self._save_snapshot.get()
            if expected is None or expected.credential is None:
                raise _error("Grok credentials must be loaded before they are saved.", "account_store_conflict")
            self._require_same_account(expected.credential, refreshed)
            document = dict(expected.snapshot.document or {})
            document[self.scope] = _merge_entry(document.get(self.scope), refreshed)
            await write_json_if_unchanged(
                self._path(),
                document,
                expected.snapshot.digest,
                provider=Provider.GROK,
            )
            self._save_snapshot.set(None)

    async def login(
        self,
        login: GrokLoginCallback,
        *,
        allow_account_switch: bool = False,
    ) -> AccountProjection:
        initial = await self._load()
        try:
            result = _validate_callback_credential(
                await login(
                    GrokLoginRequest(scope=self.scope, replacing_shared_account=initial.credential is not None)
                ),
                self.scope,
            )
        except AccountStoreError:
            raise
        except Exception:
            raise _error("The Grok OAuth login failed.", "oauth_login_failed") from None
        if (
            initial.credential is not None
            and not _same_account(initial.credential, result)
            and not allow_account_switch
        ):
            raise _error(
                "Replacing the shared Grok account requires explicit confirmation.",
                "account_switch_confirmation_required",
            )

        latest = await self._load()
        if initial.credential is not None and latest.credential is not None:
            self._require_same_account(initial.credential, latest.credential)
        elif initial.credential is not None and latest.credential is None:
            raise AccountStoreConflictError(
                "The shared Grok account changed during login.",
                code="account_store_conflict",
                provider=Provider.GROK,
            )
        if (
            latest.credential is not None
            and _same_account(latest.credential, result)
            and _newer(latest.credential, result)
        ):
            return await self.inspect()
        if latest.credential is not None and not _same_account(latest.credential, result):
            if initial.snapshot.digest != latest.snapshot.digest or not allow_account_switch:
                raise AccountStoreConflictError(
                    "A different shared Grok account appeared during login.",
                    code="account_store_conflict",
                    provider=Provider.GROK,
                )
        document = dict(latest.snapshot.document or {})
        document[self.scope] = _merge_entry(document.get(self.scope), result)
        # Cancellation before publication prevents the write. After this boundary,
        # complete persistence and its projection before reporting the outcome.
        await checkpoint()
        projection: AccountProjection | None = None
        with CancelScope(shield=True):
            await write_json_if_unchanged(self._path(), document, latest.snapshot.digest, provider=Provider.GROK)
            projection = await self.inspect()
        assert projection is not None
        return projection

    async def logout(self) -> bool:
        """Remove this shared Grok OAuth scope with a CAS write."""

        loaded = await self._load()
        if loaded.credential is None:
            return False
        document = dict(loaded.snapshot.document or {})
        document.pop(self.scope, None)
        await write_json_if_unchanged(self._path(), document, loaded.snapshot.digest, provider=Provider.GROK)
        return True

    @staticmethod
    def _require_same_account(expected: GrokCredentials, actual: GrokCredentials) -> None:
        if not _same_account(expected, actual):
            raise AccountStoreConflictError(
                "The active shared Grok account changed during the operation.",
                code="account_identity_changed",
                provider=Provider.GROK,
            )


__all__ = [
    "GrokAccountStore",
    "GrokLoginCallback",
    "GrokLoginRequest",
    "resolve_grok_policy",
]
