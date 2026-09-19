"""Process-local, bounded login sessions shared by interactive Host surfaces."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from a13n_harness.providers.model.oauth import (
    CodexLoginResult,
    DeviceAuthorizationError,
    GrokCredentials,
    GrokDeviceAuthorizationFlow,
    GrokOAuthFlow,
)
from anyio import CancelScope, Event, fail_after
from anyio.abc import TaskGroup

from a13n_harness_ui.configuration.models import StrictModel
from a13n_harness_ui.errors import HarnessUiError

from .codex import CodexAccountStore, CodexLoginRequest
from .grok import (
    DEFAULT_GROK_OAUTH_SCOPE,
    DEFAULT_GROK_OAUTH_SCOPES,
    DEFAULT_GROK_OIDC_SCOPES,
    GrokAccountStore,
    GrokLoginRequest,
)
from .models import AccountStoreError, Provider


class LoginRequest(StrictModel):
    provider: Literal["codex", "grok"]
    method: Literal["device", "browser"] = "device"
    allow_account_switch: bool = False


class LoginStatus(StrictModel):
    session_id: str
    provider: Literal["codex", "grok"]
    method: Literal["device", "browser"]
    state: Literal["starting", "waiting", "succeeded", "failed", "cancelled", "expired"] = "starting"
    verification_url: str | None = None
    user_code: str | None = None
    expires_in: int = 900
    error_code: str | None = None
    message: str | None = None


@dataclass
class _Session:
    status: LoginStatus
    scope: CancelScope
    done: Event


async def authorize_codex(request: CodexLoginRequest, method: str, present: Callable[..., None]) -> CodexLoginResult:
    from a13n_harness.providers.model.oauth import CodexDeviceAuthorizationFlow, CodexLoginFlow

    del request
    if method == "device":
        grant = await CodexDeviceAuthorizationFlow.start()
        present(verification_url=grant.verification_uri, user_code=grant.user_code, expires_in=grant.expires_in)
        return await grant.wait_for_login()
    flow = CodexLoginFlow()
    present(
        verification_url=flow.authorization_url(),
        message="The browser must reach this Host at localhost:1455. Use device authorization for a remote Host.",
    )
    with fail_after(900):
        return await flow.exchange_login_from_callback()


async def authorize_grok(request: GrokLoginRequest, method: str, present: Callable[..., None]) -> GrokCredentials:
    issuer, client_id = request.scope.rsplit("::", 1)
    scopes = DEFAULT_GROK_OAUTH_SCOPES if request.scope == DEFAULT_GROK_OAUTH_SCOPE else DEFAULT_GROK_OIDC_SCOPES
    if method == "device":
        grant = await GrokDeviceAuthorizationFlow.start(
            issuer=issuer, client_id=client_id, scopes=scopes, referrer="a13n-harness-ui"
        )
        present(
            verification_url=grant.verification_uri_complete or grant.verification_uri,
            user_code=grant.user_code,
            expires_in=min(grant.expires_in, 900),
        )
        return await grant.wait_for_credentials()
    flow = await GrokOAuthFlow.discover(issuer=issuer, client_id=client_id, scopes=scopes, referrer="a13n-harness-ui")
    present(
        verification_url=flow.authorization_url(),
        message="The browser must reach this Host's loopback callback. Use device authorization for a remote Host.",
    )
    return await flow.exchange_code_from_callback(timeout_seconds=900)


class LoginSessions:
    def __init__(self, tasks: TaskGroup, account: Callable[[Provider], CodexAccountStore | GrokAccountStore]) -> None:
        self._tasks = tasks
        self._account = account
        self._sessions: dict[str, _Session] = {}

    def start(self, request: LoginRequest) -> LoginStatus:
        if any(not session.done.is_set() for session in self._sessions.values()):
            raise HarnessUiError("A login is already active. Finish or cancel it first.", code="login_active")
        account = self._account(Provider(request.provider))
        while len(self._sessions) >= 16:
            del self._sessions[next(iter(self._sessions))]
        status = LoginStatus(
            session_id="login-" + secrets.token_hex(12), provider=request.provider, method=request.method
        )
        session = _Session(status, CancelScope(), Event())
        self._sessions[status.session_id] = session
        self._tasks.start_soon(self._run, session, request, account)
        return status

    def _get(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise HarnessUiError("The login session is unavailable. Start a new login.", code="login_not_found")
        return session

    def active(self) -> LoginStatus | None:
        return next((session.status for session in self._sessions.values() if not session.done.is_set()), None)

    def status(self, session_id: str) -> LoginStatus:
        return self._get(session_id).status

    async def cancel(self, session_id: str) -> LoginStatus:
        session = self._get(session_id)
        session.scope.cancel()
        await session.done.wait()
        return session.status

    async def _run(
        self, session: _Session, request: LoginRequest, account: CodexAccountStore | GrokAccountStore
    ) -> None:
        method = request.method

        def present(**values: object) -> None:
            session.status = session.status.model_copy(update={"state": "waiting", **values})

        async def authorize[T](operation: Awaitable[T]) -> T:
            try:
                return await operation
            except DeviceAuthorizationError as exc:
                session.status = session.status.model_copy(
                    update={
                        "state": "expired" if exc.reason == "expired" else "failed",
                        "error_code": f"device_authorization_{exc.reason}",
                        "message": f"Device authorization {exc.reason}. No fallback was started.",
                    }
                )
                raise

        try:
            with session.scope:
                with fail_after(900):
                    if isinstance(account, CodexAccountStore):
                        await account.login(
                            lambda request: authorize(authorize_codex(request, method, present)),
                            allow_account_switch=request.allow_account_switch,
                        )
                    else:
                        await account.login(
                            lambda request: authorize(authorize_grok(request, method, present)),
                            allow_account_switch=request.allow_account_switch,
                        )
                    session.status = session.status.model_copy(update={"state": "succeeded"})
            if session.scope.cancel_called and session.status.state != "succeeded":
                session.status = session.status.model_copy(update={"state": "cancelled"})
        except TimeoutError:
            session.status = session.status.model_copy(
                update={"state": "expired", "message": "The login expired. Start a new login."}
            )
        except Exception as exc:
            if session.status.error_code is not None:
                return
            code = exc.code if isinstance(exc, HarnessUiError | AccountStoreError) else "oauth_login_failed"
            session.status = session.status.model_copy(
                update={
                    "state": "failed",
                    "error_code": code,
                    "message": "Login did not complete. Check account status before retrying; no fallback was started.",
                }
            )
        finally:
            if session.status.state in {"starting", "waiting"}:
                session.status = session.status.model_copy(update={"state": "cancelled"})
            session.status = session.status.model_copy(update={"verification_url": None, "user_code": None})
            session.done.set()
