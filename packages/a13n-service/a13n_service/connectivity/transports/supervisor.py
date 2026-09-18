"""Role-owned app connections with bounded discovery and transactional fencing."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.http import cookie_free_jar
from a13n_service.connectivity.ingress.admission import AccountSnapshot, IngressEventService
from a13n_service.connectivity.ingress.provider import ProviderRequestDecision
from a13n_service.connectivity.providers.lark.adapter import LarkAccountConfig
from a13n_service.connectivity.providers.lark.wire import LarkIdentity
from a13n_service.connectivity.providers.lark.wire import normalize_payload as normalize_lark
from a13n_service.connectivity.providers.slack.adapter import SlackAccountConfig, interaction_installation
from a13n_service.connectivity.providers.slack.adapter import normalize_payload as normalize_slack
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from .clients import TransportError, lark_connection, slack_connection
from .configuration import connection_key, validate_credentials
from .leases import ConnectionClaim, ConnectionLeases

logger = logging.getLogger("a13n_service.connectivity.transports")
RENEW_INTERVAL_SECONDS = 8


@dataclass(frozen=True, slots=True)
class AccountRevision:
    id: str
    version: int
    generation: int


class EventConnectionSupervisor:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], ingress: IngressEventService, *, owner: str) -> None:
        self.sessions = sessions
        self.ingress = ingress
        self.leases = ConnectionLeases(sessions, owner)
        self._draining = asyncio.Event()
        self._running: dict[str, tuple[tuple[AccountRevision, ...], asyncio.Task[None]]] = {}

    def is_draining(self) -> bool:
        return self._draining.is_set()

    def drain(self) -> None:
        self._draining.set()
        for _, task in self._running.values():
            task.cancel()

    async def _accounts(self) -> dict[str, tuple[AccountRevision, ...]]:
        groups: dict[str, list[AccountRevision]] = {}
        async with short_session(self.sessions) as session:
            accounts = await session.scalars(
                select(AccountRecord)
                .where(
                    AccountRecord.deleted_at.is_(None),
                    AccountRecord.status == "active",
                    AccountRecord.provider_key.in_(("slack", "lark")),
                )
                .order_by(AccountRecord.id)
            )
            for account in accounts:
                if account.provider_config_json.get("event_transport") == "websocket":
                    key = connection_key(account.provider_key, account.provider_config_json)
                    groups.setdefault(key, []).append(
                        AccountRevision(account.id, account.version, account.credential_generation)
                    )
        return {key: tuple(accounts) for key, accounts in groups.items()}

    async def run(self) -> None:
        running = self._running
        async with httpx2.AsyncClient(timeout=15, follow_redirects=False, cookies=cookie_free_jar()) as client:
            try:
                while not self.is_draining():
                    try:
                        desired = await self._accounts()
                        for key, (revisions, task) in tuple(running.items()):
                            if task.done() or desired.get(key) != revisions:
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                                del running[key]
                        for key, revisions in desired.items():
                            if self.is_draining():
                                break
                            if key not in running:
                                claim = await self.leases.claim(
                                    key, {revision.id: revision.version for revision in revisions}
                                )
                                if claim is not None and self.is_draining():
                                    await self.leases.release(claim)
                                    break
                                if claim is not None:
                                    running[key] = (
                                        revisions,
                                        asyncio.create_task(self._owned_connection(client, claim, revisions)),
                                    )
                    except Exception:
                        # Never emit provider URLs, credential bodies or exception text.
                        logger.warning("Event connection reconciliation failed")
                        for _, task in running.values():
                            task.cancel()
                        await asyncio.gather(*(task for _, task in running.values()), return_exceptions=True)
                        running.clear()
                    try:
                        await asyncio.wait_for(self._draining.wait(), timeout=5)
                    except TimeoutError:
                        pass
            finally:
                for _, task in running.values():
                    task.cancel()
                await asyncio.gather(*(task for _, task in running.values()), return_exceptions=True)

    async def _owned_connection(
        self, client: httpx2.AsyncClient, claim: ConnectionClaim, revisions: tuple[AccountRevision, ...]
    ) -> None:
        async def renew() -> None:
            while True:
                await asyncio.sleep(RENEW_INTERVAL_SECONDS)
                await self.leases.update(claim, renew=True)

        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(renew())
                tasks.create_task(self._reconnect(client, claim, revisions))
        except Exception:
            logger.warning("Event connection stopped", extra={"connection_key": claim.key})
        finally:
            try:
                await self.leases.release(claim)
            except Exception:
                logger.warning("Event connection release failed")

    async def _reconnect(
        self, client: httpx2.AsyncClient, claim: ConnectionClaim, revisions: tuple[AccountRevision, ...]
    ) -> None:
        failures = 0
        while True:
            try:
                accounts = []
                credentials = []
                for revision in revisions:
                    snapshot, secret = await self.ingress.load_socket_account(revision.id)
                    if (snapshot.version, snapshot.credential_generation) != (revision.version, revision.generation):
                        raise TransportError("configuration_changed")
                    validate_credentials(snapshot.provider_key, snapshot.provider_config, secret)
                    accounts.append(snapshot)
                    credentials.append(secret)
                first = accounts[0]
                credential_key = "app_token" if first.provider_key == "slack" else "app_secret"
                values = {secret.get(credential_key) for secret in credentials}
                if len(values) != 1:
                    raise TransportError("app_credentials_conflict")
                secret = credentials[0].get(credential_key)
                if not isinstance(secret, str):
                    raise TransportError("credentials_or_permissions_invalid")

                async def connected() -> None:
                    nonlocal failures
                    failures = 0
                    await self.leases.update(claim, state="connected")

                async def admit(payload: JsonObject, accounts: list[AccountSnapshot] = accounts) -> JsonObject | None:
                    if self.is_draining():
                        raise TransportError("service_draining")
                    result = None
                    for snapshot in accounts:
                        if _matches(snapshot, payload):
                            response = await self.ingress.receive_socket(
                                snapshot=snapshot, decision=_normalize(snapshot, payload), claim=claim
                            )
                            if response:
                                result = response
                    await self.leases.update(claim, event_at=utc_now())
                    return result

                await self.leases.update(claim, state="connecting")
                if first.provider_key == "slack":
                    await slack_connection(
                        client,
                        token=secret,
                        app_id=str(first.provider_config["api_app_id"]),
                        admit=admit,
                        connected=connected,
                    )
                else:
                    config = LarkAccountConfig.model_validate(first.provider_config)
                    await lark_connection(
                        client,
                        origin=config.open_api_origin,
                        app_id=config.app_id,
                        secret=secret,
                        admit=admit,
                        connected=connected,
                    )
                await self.leases.update(claim, state="reconnecting")
            except Exception as error:
                code = error.code if isinstance(error, TransportError) else "connection_failed"
                await self.leases.update(claim, state="reconnecting", error_code=code)
            failures += 1
            await asyncio.sleep(min(60, 2 ** min(failures, 6)) + random.uniform(0, 1))


def _matches(snapshot: AccountSnapshot, payload: JsonObject) -> bool:
    config = snapshot.provider_config
    if snapshot.provider_key == "slack":
        team, enterprise = (
            interaction_installation(payload)
            if payload.get("type") == "block_actions"
            else (payload.get("team_id"), payload.get("enterprise_id"))
        )
        return (
            payload.get("api_app_id") == config.get("api_app_id")
            and team == config.get("team_id")
            and enterprise == config.get("enterprise_id")
        )
    header = payload.get("header")
    return (
        isinstance(header, dict)
        and header.get("app_id") == config.get("app_id")
        and header.get("tenant_key") == config.get("tenant_key")
    )


def _normalize(snapshot: AccountSnapshot, payload: JsonObject) -> ProviderRequestDecision:
    if snapshot.provider_key == "slack":
        return normalize_slack(payload, SlackAccountConfig.model_validate(snapshot.provider_config), utc_now())
    config = LarkAccountConfig.model_validate(snapshot.provider_config)
    return normalize_lark(
        payload, identity=LarkIdentity(config.app_id, config.tenant_key, config.bot_open_id), received_at=utc_now()
    )
