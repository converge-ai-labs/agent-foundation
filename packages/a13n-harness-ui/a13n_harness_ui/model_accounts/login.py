"""Process-local, bounded login sessions shared by interactive Host surfaces."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Literal

from a13n_harness.providers.model.oauth import (
    CodexLoginResult,
    CopilotCredentials,
    DeviceAuthorizationError,
    GrokCredentials,
    GrokDeviceAuthorizationFlow,
    GrokOAuthFlow,
)
from a13n_harness.providers.model.oauth.chatgpt import OpenAIChatGPTOAuthFlow
from anyio import CancelScope, Event, create_task_group, create_tcp_listener, fail_after
from anyio.abc import SocketAttribute, TaskGroup
from pydantic import Field, SecretStr
from pydantic_ai.exceptions import UserError

from a13n_harness_ui.configuration.models import StrictModel
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_authoring import account_connection

from .callback import receive_callback
from .chatgpt import ChatGPTAccountStore
from .codex import CodexAccountStore, CodexLoginRequest
from .copilot import CopilotAccountStore, CopilotLoginRequest
from .grok import (
    DEFAULT_GROK_OAUTH_SCOPE,
    DEFAULT_GROK_OAUTH_SCOPES,
    DEFAULT_GROK_OIDC_SCOPES,
    GrokAccountStore,
    GrokLoginRequest,
)
from .models import AccountStoreError, Provider


class LoginRequest(StrictModel):
    provider: Literal["chatgpt", "codex", "grok", "copilot"]
    method: Literal["device", "browser", "manual_callback"] = "device"
    allow_account_switch: bool = False


class LoginCallbackInput(StrictModel):
    callback_url: SecretStr = Field(min_length=1, max_length=16384, repr=False)


class LoginStatus(StrictModel):
    session_id: str
    provider: Literal["chatgpt", "codex", "grok", "copilot"]
    method: Literal["device", "browser", "manual_callback"]
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
    callback_ready: Event = field(default_factory=Event)
    callback_url: str | None = field(default=None, repr=False)
    flow: OpenAIChatGPTOAuthFlow | None = field(default=None, repr=False)


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


async def authorize_copilot(
    request: CopilotLoginRequest, method: str, present: Callable[..., None]
) -> CopilotCredentials:
    import httpx2
    from a13n_harness.providers.model.oauth.copilot import copilot_account_id
    from pydantic_ai.providers.github_copilot import GitHubCopilotOAuthFlow

    if method != "device":
        raise HarnessUiError("Copilot supports device authorization only.", code="login_method_unsupported")
    # This is the directly verified CLI device-flow scope baseline, not a claim
    # of minimal permissions. The authorization page shows the requested access.
    async with httpx2.AsyncClient(follow_redirects=False) as client:
        flow = GitHubCopilotOAuthFlow(
            client_id=request.client_id, scope="read:user,read:org,repo,gist", http_client=client
        )
        challenge = await flow.start()
        present(
            verification_url=challenge.verification_uri,
            user_code=challenge.user_code,
            expires_in=min(challenge.expires_in, 900),
            message="Uses the official Copilot CLI application identity. Review GitHub's requested permissions, including repository access. Authorization does not verify Copilot entitlement.",
        )
        credentials = await flow.wait_for_authorization()
        account_id = await copilot_account_id(credentials, http_client=client)
        return CopilotCredentials.issued(credentials, account_id=account_id, client_id=request.client_id)


class LoginSessions:
    def __init__(
        self,
        tasks: TaskGroup,
        account: Callable[[Provider], ChatGPTAccountStore | CodexAccountStore | GrokAccountStore | CopilotAccountStore],
    ) -> None:
        self._tasks = tasks
        self._account = account
        self._sessions: dict[str, _Session] = {}

    def start(self, request: LoginRequest) -> LoginStatus:
        if request.method not in account_connection(request.provider).login_methods:
            raise HarnessUiError("This account does not support that login method.", code="login_method_unsupported")
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

    def submit_callback(self, session_id: str, callback_url: str) -> LoginStatus:
        session = self._get(session_id)
        if session.status.state != "waiting" or session.flow is None or session.callback_ready.is_set():
            raise HarnessUiError("This sign-in is not waiting for a callback.", code="login_callback_unavailable")
        try:
            session.flow.validate_callback(callback_url)
        except UserError:
            raise HarnessUiError(
                "The callback URL does not match this sign-in. Paste the complete URL from the browser address bar.",
                code="login_callback_invalid",
            ) from None
        session.callback_url = callback_url
        session.callback_ready.set()
        return session.status

    async def _chatgpt(
        self, session: _Session, account: ChatGPTAccountStore, request: LoginRequest, present: Callable[..., None]
    ) -> None:
        async with AsyncExitStack() as resources:
            listener = None
            redirect_uri = "http://127.0.0.1:1456/auth/callback"
            if request.method == "browser":
                # Bind before exposing the URL; manual paste needs no local socket.
                listener = await resources.enter_async_context(
                    await create_tcp_listener(local_host="127.0.0.1", local_port=0)
                )
                port = listener.extra(SocketAttribute.local_address)[1]
                redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
            session.flow = await account.begin(
                session.status.session_id, redirect_uri, new_registration=request.allow_account_switch
            )
            async with create_task_group() as tasks:
                if listener is not None:

                    async def handle(stream):
                        await receive_callback(
                            stream, redirect_uri, lambda url: self.submit_callback(session.status.session_id, url)
                        )

                    tasks.start_soon(listener.serve, handle)
                present(
                    verification_url=session.flow.authorization_url(),
                    expires_in=600,
                    message=(
                        "After sign-in, paste the complete callback URL from the browser address bar, even if the loopback page cannot be reached. Never share that URL."
                        if request.method == "manual_callback"
                        else "If the automatic callback cannot reach this Host, paste the complete callback URL from the browser address bar. Never share that URL."
                    ),
                )
                await session.callback_ready.wait()
                assert session.callback_url is not None
                await account.complete(session.status.session_id, session.callback_url)
                tasks.cancel_scope.cancel()

    async def cancel(self, session_id: str) -> LoginStatus:
        session = self._get(session_id)
        session.scope.cancel()
        await session.done.wait()
        return session.status

    async def _run(
        self,
        session: _Session,
        request: LoginRequest,
        account: ChatGPTAccountStore | CodexAccountStore | GrokAccountStore | CopilotAccountStore,
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
                    if isinstance(account, ChatGPTAccountStore):
                        with fail_after(600):
                            await self._chatgpt(session, account, request, present)
                    elif isinstance(account, CodexAccountStore):
                        await account.login(
                            lambda request: authorize(authorize_codex(request, method, present)),
                            allow_account_switch=request.allow_account_switch,
                        )
                    elif isinstance(account, CopilotAccountStore):
                        await account.login(
                            lambda request: authorize(authorize_copilot(request, method, present)),
                            allow_account_switch=request.allow_account_switch,
                        )
                    elif isinstance(account, GrokAccountStore):
                        await account.login(
                            lambda request: authorize(authorize_grok(request, method, present)),
                            allow_account_switch=request.allow_account_switch,
                        )
                    else:
                        raise HarnessUiError("No login flow is registered for this account.", code="login_unsupported")
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
            session.callback_url = None
            session.flow = None
            if isinstance(account, ChatGPTAccountStore):
                with CancelScope(shield=True):
                    await account.cancel(session.status.session_id)
            if session.status.state in {"starting", "waiting"}:
                session.status = session.status.model_copy(update={"state": "cancelled"})
            session.status = session.status.model_copy(update={"verification_url": None, "user_code": None})
            session.done.set()
