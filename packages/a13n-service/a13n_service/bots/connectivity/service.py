"""Bounded Bot checks with pre/post authorization and credential-generation fencing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import anyio
import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.accounts.domain import Account, UpdateAccountRequest
from a13n_service.connectivity.accounts.queries import require_account
from a13n_service.connectivity.accounts.service import AccountService
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.http import ConnectivityHttpError, EndpointValidator
from a13n_service.connectivity.inspection import ConversationPage, InstallationInfo
from a13n_service.connectivity.native_management import authorize, require_limit, require_version
from a13n_service.connectivity.providers.github.inspection import discover_user
from a13n_service.connectivity.providers.github.polling_config import POLLING_VERSION
from a13n_service.connectivity.providers.github.polling_models import GitHubPollRecord
from a13n_service.connectivity.providers.github.rest import GitHubREST
from a13n_service.connectivity.providers.lark.client import LarkNativeClient
from a13n_service.connectivity.providers.lark.token import LarkTenantTokenProvider
from a13n_service.credentials import CredentialSnapshot
from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.secrets import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .collection import BotCollection, BotPlatform, BotSetupCondition, BotSummary, get_bot_summary, list_bots
from .domain import (
    ActivateBotRequest,
    BotCheck,
    BotCheckHistory,
    BotCheckRequest,
    BotSetup,
    DiscoverFeishuInstallationRequest,
    DiscoverGitHubUserRequest,
)
from .history import BotThreadCollection, list_bot_threads
from .models import BotCheckRecord
from .probe import InstallationProbe
from .reply_queries import BotReplyCollection, list_bot_replies
from .setup_tests import BotTest, BotTestHistory, CreateBotTest, create_bot_test, get_bot_test


@dataclass(frozen=True, repr=False)
class _Snapshot:
    account: Account
    credentials: CredentialSnapshot
    started_at: datetime
    conversation_id: str | None
    target_version: tuple[str, int] | None


class BotService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        protector: SecretProtector,
        *,
        public_origin: str | None = None,
        accounts: AccountService | None = None,
        timeout_seconds: float = 20,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._http_client = http_client
        self._endpoint_validator = endpoint_validator
        self._protector = protector
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._public_origin = public_origin
        self._accounts = accounts

    async def discover_feishu_installation(
        self, *, actor: AuthenticatedActor, workspace_id: str, request: DiscoverFeishuInstallationRequest
    ) -> InstallationInfo:
        async with transaction(self._sessions) as session:
            await authorize(session, actor, workspace_id, WorkspaceAction.application_account_manage)
        # Discovery accepts credentials, never a caller-controlled destination or claimed identity.
        origin = "https://open.feishu.cn"
        tokens = LarkTenantTokenProvider(
            self._http_client,
            self._endpoint_validator,
            open_api_origin=origin,
            app_id=request.app_id,
            app_secret=request.app_secret.get_secret_value(),
        )
        client = LarkNativeClient(self._http_client, self._endpoint_validator, tokens, open_api_origin=origin)
        try:
            with anyio.fail_after(self._timeout_seconds):
                installation = await client.inspect_installation()
                if not installation.enabled:
                    raise ConnectivityHttpError("bot_inactive")
        except (ConnectivityHttpError, TimeoutError) as error:
            code = error.code if isinstance(error, ConnectivityHttpError) else "provider_unavailable"
            message = (
                "Enable and publish the Feishu bot before connecting it."
                if code == "bot_inactive"
                else "Could not identify the Feishu app. Check its credentials, publication status, and tenant information permission."
            )
            raise NativeError(code, message, category=ErrorCategory.unavailable) from error
        async with transaction(self._sessions) as session:
            await authorize(session, actor, workspace_id, WorkspaceAction.application_account_manage)
        return installation

    async def discover_github_user(
        self, *, actor: AuthenticatedActor, workspace_id: str, request: DiscoverGitHubUserRequest
    ) -> InstallationInfo:
        async with transaction(self._sessions) as session:
            await authorize(session, actor, workspace_id, WorkspaceAction.application_account_manage)
        try:
            with anyio.fail_after(self._timeout_seconds):
                result = await discover_user(
                    GitHubREST(self._http_client, self._endpoint_validator, "https://api.github.com"),
                    request.personal_access_token.get_secret_value(),
                )
        except (ConnectivityHttpError, TimeoutError) as error:
            raise NativeError(
                error.code if isinstance(error, ConnectivityHttpError) else "provider_unavailable",
                "Could not verify the GitHub account. Supply a classic PAT with notification access.",
                category=ErrorCategory.unavailable,
            ) from error
        async with transaction(self._sessions) as session:
            await authorize(session, actor, workspace_id, WorkspaceAction.application_account_manage)
        return result

    async def summary(self, *, actor: AuthenticatedActor, account_id: str) -> BotSummary:
        return await get_bot_summary(self._sessions, actor=actor, account_id=account_id)

    async def collection(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
        platform: BotPlatform | None,
        condition: BotSetupCondition | None,
        search: str | None,
    ) -> BotCollection:
        return await list_bots(
            self._sessions,
            actor=actor,
            workspace_id=workspace_id,
            limit=limit,
            cursor=cursor,
            platform=platform,
            condition=condition,
            search=search,
        )

    async def create_test(
        self,
        *,
        actor: AuthenticatedActor,
        account_id: str,
        request: CreateBotTest,
        idempotency_key: str,
    ) -> BotTest:
        return await create_bot_test(
            self._sessions,
            actor=actor,
            account_id=account_id,
            request=request,
            idempotency_key=idempotency_key,
            clock=self._clock,
        )

    async def test(self, *, actor: AuthenticatedActor, account_id: str, test_id: str | None) -> BotTestHistory:
        return await get_bot_test(
            self._sessions, actor=actor, account_id=account_id, test_id=test_id, clock=self._clock
        )

    async def replies(
        self, *, actor: AuthenticatedActor, account_id: str, run_id: str, limit: int, cursor: str | None
    ) -> BotReplyCollection:
        return await list_bot_replies(
            self._sessions,
            actor=actor,
            account_id=account_id,
            run_id=run_id,
            limit=limit,
            cursor=cursor,
            clock=self._clock,
        )

    async def threads(
        self, *, actor: AuthenticatedActor, account_id: str, target_id: str | None, limit: int, cursor: str | None
    ) -> BotThreadCollection:
        return await list_bot_threads(
            self._sessions, actor=actor, account_id=account_id, target_id=target_id, limit=limit, cursor=cursor
        )

    async def activate(self, *, actor: AuthenticatedActor, account_id: str, request: ActivateBotRequest) -> Account:
        if self._accounts is None:
            raise NativeError(
                "bot_activation_unavailable", "Bot activation is unavailable.", category=ErrorCategory.unavailable
            )
        snapshot = await self._snapshot(
            actor, account_id, expected_version=request.expected_version, conversation_id=request.conversation_id
        )
        if snapshot.target_version != (request.target_id, request.target_version):
            raise NativeError(
                "target_changed",
                "The pilot conversation changed. Reload it before enabling reception.",
                category=ErrorCategory.conflict,
            )
        async with transaction(self._sessions) as session:
            await self._revalidate(session, actor, snapshot)
            await _require_pilot(session, snapshot)
        try:
            with anyio.fail_after(self._timeout_seconds):
                probe = self._probe(snapshot)
                installation = await probe.installation()
                if not installation.enabled:
                    raise ConnectivityHttpError("bot_inactive")
                conversation = await probe.conversation(request.conversation_id)
                if conversation.is_member is not True:
                    raise ConnectivityHttpError(
                        "bot_not_in_conversation" if conversation.is_member is False else "bot_membership_unverified"
                    )
                if conversation.is_active is not True:
                    raise ConnectivityHttpError("bot_conversation_inactive")
        except (ConnectivityHttpError, TimeoutError) as error:
            code = error.code if isinstance(error, ConnectivityHttpError) else "provider_unavailable"
            messages = {
                "bot_inactive": "Enable or publish the app on the provider before enabling reception.",
                "bot_not_in_conversation": "Invite the bot to this conversation, then verify and enable reception again.",
                "bot_membership_unverified": "The provider did not confirm bot membership. Check app permissions before enabling reception.",
                "bot_conversation_inactive": "The pilot conversation is inactive or could not be confirmed active. Choose an active conversation.",
            }
            raise NativeError(
                code,
                messages.get(code, "The pilot conversation could not be verified. Reception remains unchanged."),
                category=ErrorCategory.unavailable,
            ) from error
        async with transaction(self._sessions) as session:
            await self._revalidate(session, actor, snapshot)
            await _require_pilot(session, snapshot)
            return await self._accounts.update_in_session(
                session,
                actor=actor,
                account_id=account_id,
                request=UpdateAccountRequest(
                    expected_version=request.expected_version,
                    receive_enabled=True,
                    default_agent_id=request.agent_id,
                    execution_service_account_id=request.execution_service_account_id,
                    provider_policy=request.policy.model_dump(mode="json", exclude_unset=True),
                ),
            )

    async def setup(self, *, actor: AuthenticatedActor, account_id: str) -> BotSetup:
        async with transaction(self._sessions) as session:
            account = await require_account(session, account_id)
            await authorize(session, actor, account.workspace_id, WorkspaceAction.application_account_manage)
            if account.provider_key not in {"slack", "lark", "github"}:
                raise NativeError(
                    "bot_provider_unsupported",
                    "Bot setup supports Slack, Feishu, and GitHub accounts.",
                    category=ErrorCategory.invalid_request,
                )
            if account.provider_config_version == POLLING_VERSION:
                poll = await session.get(GitHubPollRecord, account_id)
                return BotSetup(
                    account_id=account_id,
                    reception_mode="polling",
                    event_path=None,
                    event_url=None,
                    poll_checked_at=poll.checked_at if poll else None,
                    poll_error_code=poll.error_code if poll else None,
                )
        path = f"/connectivity/v1/accounts/{account_id}/events"
        # Only deployment configuration supplies the origin, never browser/forwarded headers.
        return BotSetup(
            account_id=account_id,
            event_path=path,
            event_url=f"{self._public_origin.rstrip('/')}{path}" if self._public_origin else None,
        )

    async def check(self, *, actor: AuthenticatedActor, account_id: str, request: BotCheckRequest) -> BotCheck:
        snapshot = await self._snapshot(
            actor, account_id, expected_version=request.expected_version, conversation_id=request.conversation_id
        )
        identity = None
        conversation = None
        error_code = None
        try:
            with anyio.fail_after(self._timeout_seconds):
                probe = self._probe(snapshot)
                identity = await probe.installation()
                if not identity.enabled:
                    error_code = "bot_inactive"
                elif request.conversation_id is not None:
                    conversation = await probe.conversation(request.conversation_id)
        except ConnectivityHttpError as error:
            error_code = error.code
        except TimeoutError:
            error_code = "provider_unavailable"
        result = BotCheck(
            account_id=account_id,
            credential_generation=snapshot.credentials.generation,
            checked_at=self._clock(),
            conversation_id=request.conversation_id,
            installation=identity,
            conversation=conversation,
            error_code=error_code,
        )
        async with transaction(self._sessions) as session:
            await self._revalidate(session, actor, snapshot)
            key = (account_id, request.conversation_id or "")
            record = await session.get(BotCheckRecord, key)
            if record is None:
                session.add(
                    BotCheckRecord(
                        account_id=account_id,
                        conversation_id=key[1],
                        credential_generation=snapshot.credentials.generation,
                        started_at=snapshot.started_at,
                        result_json=result.model_dump(mode="json"),
                    )
                )
            elif assume_utc(record.started_at) <= snapshot.started_at:
                record.credential_generation = snapshot.credentials.generation
                record.started_at = snapshot.started_at
                record.result_json = result.model_dump(mode="json")
        return result

    async def latest(
        self, *, actor: AuthenticatedActor, account_id: str, conversation_id: str | None = None
    ) -> BotCheckHistory:
        async with transaction(self._sessions) as session:
            account = await require_account(session, account_id)
            await authorize(session, actor, account.workspace_id, WorkspaceAction.application_account_read)
            record = await session.get(BotCheckRecord, (account_id, conversation_id or ""))
            # A replacement credential must never inherit the old check's success.
            if record is None or record.credential_generation != account.credential_generation:
                return BotCheckHistory(latest=None)
            return BotCheckHistory(latest=BotCheck.model_validate(record.result_json))

    async def conversations(
        self, *, actor: AuthenticatedActor, account_id: str, limit: int = 100, cursor: str | None = None
    ) -> ConversationPage:
        require_limit(limit)
        snapshot = await self._snapshot(actor, account_id)
        try:
            with anyio.fail_after(self._timeout_seconds):
                probe = self._probe(snapshot)
                identity = await probe.installation()
                if not identity.enabled:
                    raise ConnectivityHttpError("bot_inactive")
                result = await probe.conversations(limit=limit, cursor=cursor)
        except ConnectivityHttpError as error:
            raise NativeError(
                error.code, "The Bot's conversations could not be verified.", category=ErrorCategory.unavailable
            ) from error
        except TimeoutError as error:
            raise NativeError(
                "provider_unavailable", "The Bot provider did not respond in time.", category=ErrorCategory.unavailable
            ) from error
        async with transaction(self._sessions) as session:
            await self._revalidate(session, actor, snapshot)
        return result

    async def _snapshot(
        self,
        actor: AuthenticatedActor,
        account_id: str,
        *,
        expected_version: int | None = None,
        conversation_id: str | None = None,
    ) -> _Snapshot:
        async with transaction(self._sessions) as session:
            account = await require_account(session, account_id)
            await authorize(session, actor, account.workspace_id, WorkspaceAction.application_account_manage)
            if expected_version is not None:
                require_version(account.version, expected_version)
            if account.provider_key not in {"slack", "lark", "github"}:
                raise NativeError(
                    "bot_provider_unsupported",
                    "Bot checks support Slack, Feishu, and GitHub accounts.",
                    category=ErrorCategory.invalid_request,
                )
            target = await _target_version(session, account_id, conversation_id)
            return _Snapshot(
                account.to_resource(), account.credential_snapshot(), self._clock(), conversation_id, target
            )

    async def _revalidate(self, session: AsyncSession, actor: AuthenticatedActor, snapshot: _Snapshot) -> None:
        account = await require_account(session, snapshot.account.id, lock=True)
        await authorize(session, actor, account.workspace_id, WorkspaceAction.application_account_manage)
        require_version(account.version, snapshot.account.version)
        if account.credential_generation != snapshot.credentials.generation:
            raise NativeError(
                "credential_changed", "Credentials changed during the Bot check.", category=ErrorCategory.conflict
            )
        if await _target_version(session, account.id, snapshot.conversation_id) != snapshot.target_version:
            raise NativeError(
                "target_changed",
                "Conversation configuration changed during the check.",
                category=ErrorCategory.conflict,
            )

    def _probe(self, snapshot: _Snapshot) -> InstallationProbe:
        return InstallationProbe(
            self._http_client,
            self._endpoint_validator,
            provider_key=snapshot.account.provider_key,
            config_version=snapshot.account.provider_config_version,
            config=snapshot.account.provider_config,
            credentials=snapshot.credentials,
            protector=self._protector,
        )


async def _target_version(
    session: AsyncSession, account_id: str, conversation_id: str | None
) -> tuple[str, int] | None:
    if conversation_id is None:
        return None
    row = (
        await session.execute(
            select(AccountTargetRecord.id, AccountTargetRecord.version).where(
                AccountTargetRecord.account_id == account_id,
                AccountTargetRecord.target_kind.in_(("conversation", "repository")),
                AccountTargetRecord.external_target_id == conversation_id,
            )
        )
    ).one_or_none()
    return None if row is None else (row.id, row.version)


async def _require_pilot(session: AsyncSession, snapshot: _Snapshot) -> None:
    account = snapshot.account
    if account.status != "active" or account.receive_enabled or account.reception_scope != "configured_targets":
        raise NativeError(
            "bot_pilot_setup_required",
            "Pilot setup requires an active account with reception off and configured-target admission.",
            category=ErrorCategory.conflict,
        )
    targets = (
        await session.scalars(
            select(AccountTargetRecord)
            .where(
                AccountTargetRecord.account_id == account.id,
                AccountTargetRecord.receive_enabled.is_(True),
            )
            .limit(2)
        )
    ).all()
    if len(targets) != 1 or snapshot.target_version != (targets[0].id, targets[0].version):
        raise NativeError(
            "bot_pilot_setup_required",
            "Configure exactly one enabled pilot conversation before activation.",
            category=ErrorCategory.conflict,
        )
    target = targets[0]
    if (
        target.agent_id is not None
        or target.config_override_json is not None
        or target.provider_policy_json is not None
    ):
        raise NativeError(
            "bot_pilot_setup_required",
            "The pilot must inherit its account's agent and response policy. Review target overrides first.",
            category=ErrorCategory.conflict,
        )
