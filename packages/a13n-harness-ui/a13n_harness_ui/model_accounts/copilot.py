"""Host-owned native Copilot grants, separate from API-key and configuration stores."""

from __future__ import annotations

from collections.abc import Awaitable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from a13n_harness.providers.model.oauth import CopilotCredentials, CopilotRefresh
from a13n_harness.providers.model.oauth.copilot import validate_token_envelope
from a13n_harness.providers.model.oauth.rotation import (
    grant_fingerprint,
    load_credentials,
    rotate_grant,
)
from anyio import CancelScope
from anyio.lowlevel import checkpoint
from pydantic import TypeAdapter

from ._common import (
    JsonSnapshot,
    ensure_aware,
    expiry_status,
    parse_timestamp,
    read_json_snapshot,
    timestamp_text,
    write_json_if_unchanged,
)
from .coordination import RefreshJournal, store_lock
from .copilot_cli import DEFAULT_COPILOT_CLIENT_ID, CopilotCliFile
from .models import (
    AccountCandidate,
    AccountProjection,
    AccountSelection,
    AccountStoreConflictError,
    AccountStoreError,
    Availability,
    ExpiryStatus,
    Provider,
    RequiredAction,
    StoreKind,
)


@dataclass(frozen=True, slots=True)
class CopilotLoginRequest:
    client_id: str


class CopilotLoginCallback(Protocol):
    def __call__(self, request: CopilotLoginRequest) -> Awaitable[CopilotCredentials]: ...


def _error(message: str, code: str = "account_store_incompatible") -> AccountStoreError:
    return AccountStoreError(message, code=code, provider=Provider.COPILOT)


def _encode(credential: CopilotCredentials) -> dict[str, Any]:
    """Revalidate native instances before they can cross the durable boundary."""
    from pydantic_ai.providers.github_copilot import GitHubCopilotCredentials

    if not isinstance(credential, CopilotCredentials) or not (
        isinstance(credential.account_id, str)
        and credential.account_id
        and credential.account_id.isascii()
        and all(char.isalnum() or char == "-" for char in credential.account_id)
        and credential.account_id == credential.account_id.casefold()
        and credential.client_id == DEFAULT_COPILOT_CLIENT_ID
        and credential.issuer == "https://github.com"
    ):
        raise _error("The Copilot grant does not match this account integration.")
    envelope = validate_token_envelope(credential.credentials)
    return {
        "account_id": credential.account_id,
        "client_id": credential.client_id,
        "credentials": TypeAdapter(GitHubCopilotCredentials).dump_python(envelope, mode="json"),
        "expires_at": timestamp_text(credential.expires_at) if credential.expires_at is not None else None,
        "refresh_expires_at": timestamp_text(credential.refresh_expires_at)
        if credential.refresh_expires_at is not None
        else None,
    }


def _decode(value: object, account_id: str) -> CopilotCredentials:
    from pydantic_ai.providers.github_copilot import GitHubCopilotCredentials

    try:
        if not isinstance(value, dict) or value.get("account_id") != account_id:
            raise ValueError
        credential = CopilotCredentials(
            account_id=account_id,
            client_id=value["client_id"],
            credentials=TypeAdapter(GitHubCopilotCredentials).validate_python(value["credentials"]),
            expires_at=parse_timestamp(value["expires_at"], provider=Provider.COPILOT, field="expires_at")
            if value.get("expires_at") is not None
            else None,
            refresh_expires_at=parse_timestamp(
                value["refresh_expires_at"], provider=Provider.COPILOT, field="refresh_expires_at"
            )
            if value.get("refresh_expires_at") is not None
            else None,
        )
        _encode(credential)
        return credential
    except (ValueError, KeyError, TypeError):
        raise _error("The selected Copilot grant is invalid.") from None


class CopilotAccountStore:
    """One selected account, request-fresh loading and durable rotating-grant exclusion."""

    def __init__(self, path: Path, *, cli_path: Path | None = None):
        self.path = path.resolve()
        self.cli = CopilotCliFile(cli_path)

    @property
    def native_source_id(self) -> str:
        return f"native:{self.path}"

    async def _snapshot(self) -> JsonSnapshot:
        snapshot = await read_json_snapshot(self.path, provider=Provider.COPILOT)
        document = snapshot.document
        if document is not None and (
            document.get("version") != 1
            or not isinstance(document.get("accounts"), dict)
            or (document.get("selected") is not None and not isinstance(document["selected"], str))
        ):
            raise _error("The Copilot account store has an incompatible schema.")
        if document is not None and document.get("selected") is not None:
            source = document.get("source")
            if not isinstance(source, dict) or source.get("kind") not in {"native", "copilot_cli_file"}:
                raise _error("The selected Copilot source is invalid.")
            if source["kind"] == "copilot_cli_file" and (
                not isinstance(source.get("path"), str) or not Path(source["path"]).is_absolute()
            ):
                raise _error("The selected Copilot CLI path is invalid.")
            return snapshot
        # Only first selection discovers the CLI. Persist identity, never its token.
        # Unsupported/malformed external storage must not prevent native login.
        try:
            selected = await self.cli.selected() if await self.cli.plaintext() else None
        except AccountStoreError:
            selected = None
        if selected is not None:
            updated = dict(document or {"version": 1, "accounts": {}})
            updated.update(selected=selected, source={"kind": "copilot_cli_file", "path": str(self.cli.path)})
            try:
                await write_json_if_unchanged(self.path, updated, snapshot.digest, provider=Provider.COPILOT)
            except AccountStoreConflictError:
                pass
            return await self._snapshot()
        return snapshot

    async def _selected(self, snapshot: JsonSnapshot) -> CopilotCredentials | None:
        document = snapshot.document or {}
        selected = document.get("selected")
        if selected is None:
            return None
        if document["source"]["kind"] == "copilot_cli_file":
            return await CopilotCliFile(Path(document["source"]["path"])).load(selected)
        if selected not in document["accounts"]:
            return None
        return replace(_decode(document["accounts"][selected], selected), source_id=self.native_source_id)

    def exclusive(self) -> AbstractAsyncContextManager[None]:
        return store_lock(self.path)

    async def read(self) -> tuple[CopilotCredentials, JsonSnapshot]:
        snapshot = await self._snapshot()
        credential = await self._selected(snapshot)
        if credential is None:
            raise _error("A Copilot login is required for the selected account.", "authentication_required")
        return credential, snapshot

    async def publish(self, state: JsonSnapshot, credentials: CopilotCredentials) -> None:
        document = dict(state.document or {"version": 1, "accounts": {}})
        accounts = dict(document["accounts"])
        accounts[credentials.account_id] = _encode(credentials)
        document.update(accounts=accounts, selected=credentials.account_id, source={"kind": "native"})
        await write_json_if_unchanged(self.path, document, state.digest, provider=Provider.COPILOT)

    def _journal(self) -> RefreshJournal:
        return RefreshJournal(self.path, DEFAULT_COPILOT_CLIENT_ID, provider=Provider.COPILOT)

    async def blocked(self, grant: str) -> bool:
        return await self._journal().blocked(grant)

    async def set_blocked(self, grant: str, blocked: bool) -> None:
        await self._journal().set_blocked(grant, blocked)

    async def load(self) -> CopilotCredentials:
        return await load_credentials(self)

    async def rotate(self, expected: CopilotCredentials, exchange: CopilotRefresh) -> CopilotCredentials:
        async def validated(current: CopilotCredentials) -> CopilotCredentials:
            result = await exchange(current)
            _encode(result)
            return replace(result, source_id=current.source_id)

        if expected.source_id != self.native_source_id:
            raise _error(
                "The shared Copilot CLI token cannot be refreshed here. Log in again.", "authentication_required"
            )
        return await rotate_grant(self, expected, validated)

    async def inspect(self, *, now: datetime | None = None) -> AccountProjection:
        snapshot = await self._snapshot()
        document = snapshot.document or {}
        selected = document.get("selected")
        source = document.get("source", {})
        shared = source.get("kind") == "copilot_cli_file"
        source_id = f"copilot_cli_file:{source['path']}" if shared else self.native_source_id
        try:
            credential = await self._selected(snapshot)
            if selected is None and (await self.cli.snapshot()).document is not None and not await self.cli.plaintext():
                return AccountProjection(
                    provider=Provider.COPILOT,
                    availability=Availability.UNSUPPORTED,
                    source=StoreKind.KEYRING,
                    usable=False,
                    expiry=ExpiryStatus.UNKNOWN,
                    required_action=RequiredAction.LOGIN,
                    message="CLI keychain storage is unsupported. Use native login or explicitly enable CLI plaintext storage.",
                )
        except AccountStoreError as exc:
            return AccountProjection(
                provider=Provider.COPILOT,
                availability=Availability.UNSUPPORTED
                if exc.code == "account_store_unsupported"
                else Availability.INCOMPATIBLE,
                source=StoreKind.FILE,
                usable=False,
                expiry=ExpiryStatus.UNKNOWN,
                required_action=RequiredAction.LOGIN,
                account_id=selected,
                source_id=source_id,
                shared_with_cli=shared,
                message=str(exc),
            )
        if credential is None:
            return AccountProjection(
                provider=Provider.COPILOT,
                availability=Availability.ABSENT,
                source=StoreKind.FILE,
                usable=False,
                expiry=ExpiryStatus.NOT_APPLICABLE,
                required_action=RequiredAction.LOGIN,
                account_id=selected,
                source_id=source_id,
                shared_with_cli=shared,
            )
        blocked = await self.blocked(grant_fingerprint(credential.refresh_token))
        instant = ensure_aware(now)
        expiry = expiry_status(credential.expires_at, instant, timedelta(minutes=5))
        refreshable = bool(credential.refresh_token) and (
            credential.refresh_expires_at is None or instant < credential.refresh_expires_at
        )
        usable = not blocked and (expiry in {ExpiryStatus.VALID, ExpiryStatus.UNKNOWN} or refreshable)
        return AccountProjection(
            provider=Provider.COPILOT,
            availability=Availability.AVAILABLE,
            source=StoreKind.FILE,
            usable=usable,
            expiry=expiry,
            expires_at=credential.expires_at,
            required_action=RequiredAction.NONE if usable else RequiredAction.REAUTHENTICATE,
            account_id=credential.account_id,
            source_id=source_id,
            shared_with_cli=shared,
        )

    async def login(self, login: CopilotLoginCallback, *, allow_account_switch: bool = False) -> AccountProjection:
        initial = await self._snapshot()
        initial_source = (initial.document or {}).get("source", {})
        external = (
            CopilotCliFile(Path(initial_source["path"])) if initial_source.get("kind") == "copilot_cli_file" else None
        )
        external_digest = (await external.snapshot()).digest if external is not None else None
        result = await login(CopilotLoginRequest(client_id=DEFAULT_COPILOT_CLIENT_ID))
        _encode(result)
        async with self.exclusive():
            latest = await self._snapshot()
            current_account = (latest.document or {}).get("selected")
            external_changed = external is not None and (await external.snapshot()).digest != external_digest
            if latest.digest != initial.digest or external_changed:
                raise AccountStoreConflictError(
                    "The selected Copilot account changed during login. Select the account explicitly.",
                    code="account_store_conflict",
                    provider=Provider.COPILOT,
                )
            if (
                current_account is not None
                and current_account.casefold() != result.account_id
                and not allow_account_switch
            ):
                raise _error(
                    "Confirm switching to the newly authorized Copilot account.", "account_switch_confirmation_required"
                )
            if await self.blocked(grant_fingerprint(result.refresh_token)):
                raise _error("Reauthentication did not provide a new usable grant.", "authentication_required")
            await checkpoint()
            projection: AccountProjection | None = None
            with CancelScope(shield=True):
                await self.publish(latest, result)
                projection = await self.inspect()
            assert projection is not None
            return projection

    async def candidates(self) -> tuple[AccountCandidate, ...]:
        snapshot = await self._snapshot()
        document = snapshot.document or {}
        selected, source = document.get("selected"), document.get("source", {})
        candidates = [
            AccountCandidate(
                selection=AccountSelection(source="native", account_id=account),
                label=f"{account} · Host login",
                selected=source.get("kind") == "native" and selected == account,
            )
            for account in document.get("accounts", {})
        ]
        try:
            accounts = await self.cli.accounts() if await self.cli.plaintext() else ()
            for account in accounts:
                if await self.cli.load(account) is not None:
                    candidates.append(
                        AccountCandidate(
                            selection=AccountSelection(source="copilot_cli_file", account_id=account),
                            label=f"{account} · Copilot CLI file",
                            selected=source.get("kind") == "copilot_cli_file"
                            and selected == account
                            and source.get("path") == str(self.cli.path),
                        )
                    )
        except AccountStoreError:
            pass
        return tuple(candidates)

    async def select(self, selection: AccountSelection) -> AccountProjection:
        async with self.exclusive():
            snapshot = await self._snapshot()
            document = dict(snapshot.document or {"version": 1, "accounts": {}})
            if selection.source == "native":
                value = document["accounts"].get(selection.account_id)
                if value is None:
                    raise _error("The selected Host account is missing.", "authentication_required")
                _decode(value, selection.account_id)
                source = {"kind": "native"}
            else:
                if (
                    selection.account_id not in await self.cli.accounts()
                    or await self.cli.load(selection.account_id) is None
                ):
                    raise _error("The selected CLI file account is missing.", "authentication_required")
                source = {"kind": "copilot_cli_file", "path": str(self.cli.path)}
            document.update(selected=selection.account_id, source=source)
            await write_json_if_unchanged(self.path, document, snapshot.digest, provider=Provider.COPILOT)
        return await self.inspect()

    async def logout(self) -> bool:
        async with self.exclusive():
            snapshot = await self._snapshot()
            document = dict(snapshot.document or {})
            selected = document.get("selected")
            if selected is None:
                return False
            if document["source"]["kind"] == "copilot_cli_file":
                return await CopilotCliFile(Path(document["source"]["path"])).logout(selected)
            if selected not in document["accounts"]:
                return False
            accounts = dict(document["accounts"])
            del accounts[selected]
            document["accounts"] = accounts
            # Keep the non-secret binding. Missing credentials never select another account.
            await write_json_if_unchanged(self.path, document, snapshot.digest, provider=Provider.COPILOT)
            return True
