"""Verified official Copilot CLI plaintext-file compatibility; no CLI or keychain calls."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from a13n_harness.providers.model.oauth import CopilotCredentials

from ._common import JsonSnapshot, read_json_snapshot, write_json_if_unchanged
from .models import AccountStoreError, Provider

DEFAULT_COPILOT_CLIENT_ID = "Ov23ctDVkRmgkPke0Mmm"
_ALIASES = {
    "copilot_tokens": "copilotTokens",
    "auth_tokens": "authTokens",
    "last_logged_in_user": "lastLoggedInUser",
    "logged_in_users": "loggedInUsers",
    "store_token_plaintext": "storeTokenPlaintext",
}


def incompatible() -> AccountStoreError:
    return AccountStoreError(
        "The Copilot CLI file has an unsupported account or token format.",
        code="account_store_incompatible",
        provider=Provider.COPILOT,
    )


def normalize(document: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in document.items():
        canonical = _ALIASES.get(key, key)
        if canonical in result:
            raise incompatible()
        result[canonical] = value
    return result


def account_login(user: object) -> str:
    if not isinstance(user, dict) or user.get("host") != "https://github.com":
        raise incompatible()
    login = user.get("login")
    if (
        not isinstance(login, str)
        or not login
        or not login.isascii()
        or not all(c.isalnum() or c == "-" for c in login)
    ):
        raise incompatible()
    return login


class CopilotCliFile:
    def __init__(self, path: Path | None = None):
        self.path = (path or Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot") / "config.json").resolve()

    async def plaintext(self) -> bool:
        settings = await read_json_snapshot(
            self.path.with_name("settings.json"), provider=Provider.COPILOT, comments=True
        )
        return normalize(settings.document or {}).get("storeTokenPlaintext") is True

    async def snapshot(self) -> JsonSnapshot:
        return await read_json_snapshot(self.path, provider=Provider.COPILOT, comments=True)

    async def selected(self) -> str | None:
        document = normalize((await self.snapshot()).document or {})
        user = document.get("lastLoggedInUser")
        if user is None:
            users = document.get("loggedInUsers", [])
            if not isinstance(users, list):
                raise incompatible()
            user = users[0] if users else None
        return account_login(user) if user is not None else None

    async def accounts(self) -> tuple[str, ...]:
        document = normalize((await self.snapshot()).document or {})
        users = document.get("loggedInUsers", [])
        if not isinstance(users, list):
            raise incompatible()
        selected = document.get("lastLoggedInUser")
        return tuple(
            dict.fromkeys(account_login(user) for user in ([selected] if selected is not None else []) + users)
        )

    async def load(self, login: str) -> CopilotCredentials | None:
        from pydantic_ai.providers.github_copilot import GitHubCopilotCredentials

        if not await self.plaintext():
            raise AccountStoreError(
                "Copilot CLI keychain storage is not supported. Use native login, or explicitly configure the CLI for plaintext storage.",
                code="account_store_unsupported",
                provider=Provider.COPILOT,
            )
        document = normalize((await self.snapshot()).document or {})
        key = f"https://github.com:{login}"
        legacy, modern = document.get("copilotTokens", {}), document.get("authTokens", {})
        if not isinstance(legacy, dict) or not isinstance(modern, dict):
            raise incompatible()
        old, new = legacy.get(key), modern.get(key)
        if old is not None and (not isinstance(old, str) or not old.strip()):
            raise incompatible()
        if new is not None and (
            not isinstance(new, dict)
            or set(new) != {"token"}
            or not isinstance(new["token"], str)
            or not new["token"].strip()
        ):
            raise incompatible()
        token = old if old is not None else new["token"] if new is not None else None
        if token is None:
            return None
        return CopilotCredentials(
            account_id=login.casefold(),
            client_id=DEFAULT_COPILOT_CLIENT_ID,
            source_id=f"copilot_cli_file:{self.path}",
            credentials=GitHubCopilotCredentials(access_token=token, token_type="bearer", scope=""),
        )

    async def logout(self, login: str) -> bool:
        if not await self.plaintext():
            raise AccountStoreError(
                "Copilot CLI keychain logout is unsupported. Manage that credential with the official CLI.",
                code="account_store_unsupported",
                provider=Provider.COPILOT,
            )
        snapshot = await self.snapshot()
        document = dict(snapshot.document or {})
        key = f"https://github.com:{login}"
        changed = False
        for field in ("copilotTokens", "copilot_tokens", "authTokens", "auth_tokens"):
            tokens = document.get(field)
            if isinstance(tokens, dict) and key in tokens:
                document[field] = {name: value for name, value in tokens.items() if name != key}
                changed = True
        if changed:
            # Both accepted token fields are removed: the lower-priority value must not reappear.
            # Other accounts, selection and unrelated settings remain intact. JSONC is normalized to JSON.
            await write_json_if_unchanged(self.path, document, snapshot.digest, provider=Provider.COPILOT)
        return changed
