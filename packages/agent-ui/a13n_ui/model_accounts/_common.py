"""Small shared primitives for provider-specific compatible stores."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, cast

from anyio import to_thread
from filelock import FileLock

from .models import AccountStoreConflictError, AccountStoreError, ExpiryStatus, Provider

_MAX_STORE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class JsonSnapshot:
    path: Path
    document: dict[str, Any] | None
    digest: str | None


class _DuplicateKey(ValueError):
    pass


class _ChangedDuringWrite(RuntimeError):
    pass


def _bounded_error(message: str, *, code: str, provider: Provider) -> NoReturn:
    raise AccountStoreError(message, code=code, provider=provider)


def _decode_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def decode_json_object(raw: bytes, *, provider: Provider, empty_object: bool = False) -> dict[str, Any]:
    if not raw.strip() and empty_object:
        return {}
    try:
        decoded = json.loads(
            raw,
            object_pairs_hook=_decode_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey, ValueError):
        _bounded_error(
            "The selected compatible account store is malformed.",
            code="account_store_malformed",
            provider=provider,
        )
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        _bounded_error(
            "The selected compatible account store has an incompatible root schema.",
            code="account_store_incompatible",
            provider=provider,
        )
    return cast(dict[str, Any], decoded)


def _read_bytes(path: Path) -> bytes | None:
    try:
        with path.open("rb") as source:
            raw = source.read(_MAX_STORE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > _MAX_STORE_BYTES:
        raise ValueError("oversize")
    return raw


async def read_json_snapshot(
    path: Path,
    *,
    provider: Provider,
    empty_object: bool = False,
) -> JsonSnapshot:
    try:
        raw = await to_thread.run_sync(_read_bytes, path)
    except ValueError:
        _bounded_error(
            "The selected compatible account store exceeds the supported size limit.",
            code="account_store_too_large",
            provider=provider,
        )
    except OSError:
        _bounded_error(
            "The selected compatible account store could not be read.",
            code="account_store_unavailable",
            provider=provider,
        )
    if raw is None:
        return JsonSnapshot(path=path, document=None, digest=None)
    return JsonSnapshot(
        path=path,
        document=decode_json_object(raw, provider=provider, empty_object=empty_object),
        digest=hashlib.sha256(raw).hexdigest(),
    )


def _current_digest(path: Path) -> str | None:
    raw = _read_bytes(path)
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _write_if_unchanged(path: Path, payload: bytes, expected_digest: str | None) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        with FileLock(f"{path}.lock", mode=0o600):
            if _current_digest(path) != expected_digest:
                raise _ChangedDuringWrite
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


async def write_json_if_unchanged(
    path: Path,
    document: Mapping[str, Any],
    expected_digest: str | None,
    *,
    provider: Provider,
) -> None:
    try:
        payload = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    except (TypeError, ValueError):
        _bounded_error(
            "The compatible account update has an invalid provider schema.",
            code="account_update_invalid",
            provider=provider,
        )
    try:
        await to_thread.run_sync(_write_if_unchanged, path, payload, expected_digest)
    except _ChangedDuringWrite:
        raise AccountStoreConflictError(
            "The shared compatible account store changed before the update could be committed.",
            code="account_store_conflict",
            provider=provider,
        ) from None
    except OSError:
        _bounded_error(
            "The compatible account update could not be committed.",
            code="account_store_write_failed",
            provider=provider,
        )


def parse_timestamp(value: Any, *, provider: Provider, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        _bounded_error(
            f"The selected {provider.value} account has an invalid {field} field.",
            code="account_store_incompatible",
            provider=provider,
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        _bounded_error(
            f"The selected {provider.value} account has an invalid {field} field.",
            code="account_store_incompatible",
            provider=provider,
        )
    if parsed.tzinfo is None:
        _bounded_error(
            f"The selected {provider.value} account has an invalid {field} field.",
            code="account_store_incompatible",
            provider=provider,
        )
    return parsed.astimezone(UTC)


def timestamp_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def jwt_claims(token: str, *, provider: Provider, field: str) -> dict[str, Any]:
    if not isinstance(token, str) or not token:
        _bounded_error(
            f"The selected {provider.value} account has an invalid {field} field.",
            code="account_store_incompatible",
            provider=provider,
        )
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        _bounded_error(
            f"The selected {provider.value} account has an invalid {field} field.",
            code="account_store_incompatible",
            provider=provider,
        )
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload), object_pairs_hook=_decode_object)
    except (ValueError, json.JSONDecodeError, _DuplicateKey, binascii.Error):
        _bounded_error(
            f"The selected {provider.value} account has an invalid {field} field.",
            code="account_store_incompatible",
            provider=provider,
        )
    if not isinstance(decoded, dict):
        _bounded_error(
            f"The selected {provider.value} account has an invalid {field} field.",
            code="account_store_incompatible",
            provider=provider,
        )
    return cast(dict[str, Any], decoded)


def jwt_expiry(token: str, *, provider: Provider) -> datetime:
    claims = jwt_claims(token, provider=provider, field="access_token")
    expiration = claims.get("exp")
    if not isinstance(expiration, int) or isinstance(expiration, bool):
        _bounded_error(
            f"The selected {provider.value} account access token has no valid expiry.",
            code="account_store_incompatible",
            provider=provider,
        )
    try:
        return datetime.fromtimestamp(expiration, tz=UTC)
    except (OverflowError, OSError, ValueError):
        _bounded_error(
            f"The selected {provider.value} account access token has no valid expiry.",
            code="account_store_incompatible",
            provider=provider,
        )


def expiry_status(expires_at: datetime | None, now: datetime, refresh_window: timedelta) -> ExpiryStatus:
    if expires_at is None:
        return ExpiryStatus.UNKNOWN
    if now >= expires_at:
        return ExpiryStatus.EXPIRED
    if now + refresh_window >= expires_at:
        return ExpiryStatus.EXPIRING
    return ExpiryStatus.VALID


def ensure_aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)
