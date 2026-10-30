"""Resolve one selected proxy connection inside a short read; observe it after releasing the database."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.managed import ManagedCredentialCache
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import short_session
from a13n_service.providers.environment.metrics import DeviceObservation, ScrapeTarget, observe
from a13n_service.providers.metrics import Destination
from a13n_service.resources.metrics_data_sources.schemas import Configuration
from a13n_service.resources.metrics_data_sources.service import headers
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.devices.tables import DeviceRow
from a13n_service.runs.environments import external
from a13n_service.runs.environments.adapters import ExternalAccount, Target, close, open_provider, provider_identity
from a13n_service.runs.environments.monitoring.history import Status
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.tenancy.tables import WorkspaceRow

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime


@dataclass(frozen=True, slots=True)
class ReverseTarget:
    token_hash: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Selection:
    organization_id: str
    workspace_id: str
    target_id: str
    source_id: str
    source_version: int
    revision: str
    device_id: str
    status: Status
    target: Target | ReverseTarget | None = field(repr=False)
    export: Destination | None = field(repr=False)
    export_interval: float


def source_inputs(source: DataSourceRow, config: Configuration) -> dict:
    """Only collection/export inputs fence the stream; metadata and query settings are independent."""
    return {
        "id": source.id,
        "enabled": source.enabled,
        "collection": config.collection,
        "collector": config.collector,
        "export": None if config.export is None else config.export.model_dump(mode="json"),
        "export_credentials": source.export_credentials,
    }


async def selected(
    runtime: Runtime, session: AsyncSession, environment: EnvironmentRow, source: DataSourceRow
) -> Selection:
    """The caller has established eligibility or read authority for these same-workspace rows."""
    config = Configuration.model_validate(source.config)
    assert config.collection in {"proxy", "pull"} and source.enabled
    target = None
    status: Status = "not_ready" if environment.status != "ready" else "unsupported"
    provider_inputs = None
    if environment.status == "ready":
        if environment.template_id is None:
            target = Target(environment.id, external.account(environment), {}, None)
        elif environment.provider_id is not None:
            provider_row = await session.get(EnvironmentProviderRow, environment.provider_id)
            if provider_row is not None and provider_row.enabled:
                provider_inputs = {
                    "id": provider_row.id,
                    "type": provider_row.type,
                    "config": provider_row.config,
                    "credential": provider_row.credential,
                }
                provider = await read_provider(session, EnvironmentProviderRow, provider_row.id)
                handle = Handle.model_validate(environment.handle)
                if (
                    provider.type in {"docker", "e2b"}
                    and handle.recipe.get("execution") == "envd_http"
                    and environment.provider_identity == provider_identity(runtime.registry, provider)
                ):
                    target = Target(environment.id, provider, handle.recipe, handle.state)
            else:
                status = "disabled"
        if target is not None:
            status = "waiting"
    identity = {
        "source": source_inputs(source, config),
        "generation": environment.generation,
        "device_id": environment.device_id,
        "endpoint": environment.endpoint,
        "token": environment.token,
        "handle": environment.handle,
        "provider": provider_inputs,
    }
    revision = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return Selection(
        environment.organization_id,
        environment.workspace_id,
        environment.id,
        source.id,
        source.version,
        revision,
        environment.device_id or environment.id,
        status,
        target,
        None if config.export is None else Destination(config.export.endpoint, headers(runtime.keys, source, "export")),
        30 if config.export is None else config.export.interval_seconds,
    )


async def resolve(runtime: Runtime, environment_id: str) -> Selection | None:
    async with short_session(runtime.storage) as session:
        if environment_id.startswith("dev_"):
            rows = (
                await session.execute(
                    select(DeviceRow, DataSourceRow)
                    .join(DataSourceRow, DataSourceRow.id == DeviceRow.metrics_data_source_id)
                    .join(WorkspaceRow, WorkspaceRow.id == DeviceRow.workspace_id)
                    .where(
                        DeviceRow.id == environment_id,
                        DeviceRow.enabled.is_(True),
                        DataSourceRow.enabled.is_(True),
                        DataSourceRow.config["collection"].astext == "proxy",
                        WorkspaceRow.archived_at.is_(None),
                    )
                )
            ).one_or_none()
            return None if rows is None else selected_device(runtime, rows[0], rows[1])
        rows = (
            await session.execute(
                select(EnvironmentRow, DataSourceRow)
                .join(DataSourceRow, DataSourceRow.id == EnvironmentRow.metrics_data_source_id)
                .join(WorkspaceRow, WorkspaceRow.id == EnvironmentRow.workspace_id)
                .where(
                    EnvironmentRow.id == environment_id,
                    EnvironmentRow.status != "deleted",
                    DataSourceRow.enabled.is_(True),
                    DataSourceRow.config["collection"].astext == "proxy",
                    WorkspaceRow.archived_at.is_(None),
                )
            )
        ).one_or_none()
        if rows is None:
            return None
        return await selected(runtime, session, rows[0], rows[1])


async def candidates(runtime: Runtime, *, after: str, limit: int) -> list[str]:
    async with short_session(runtime.storage) as session:
        return list(
            await session.scalars(
                select(EnvironmentRow.id)
                .join(DataSourceRow, DataSourceRow.id == EnvironmentRow.metrics_data_source_id)
                .join(WorkspaceRow, WorkspaceRow.id == EnvironmentRow.workspace_id)
                .where(
                    EnvironmentRow.id > after,
                    EnvironmentRow.status == "ready",
                    DataSourceRow.enabled.is_(True),
                    DataSourceRow.config["collection"].astext == "proxy",
                    WorkspaceRow.archived_at.is_(None),
                )
                .order_by(EnvironmentRow.id)
                .limit(limit)
            )
        )


async def scrape(runtime: Runtime, selection: Selection) -> ScrapeTarget:
    target = selection.target
    assert isinstance(target, Target)
    if isinstance(target.account, ExternalAccount):
        account = target.account
        await runtime.endpoint_policy.validate(account.endpoint)
        return ScrapeTarget(account.endpoint, SecretStr(account.reveal_token(runtime.keys)))
    provider = await open_provider(runtime, target)
    try:
        result = await provider.scrape_target(target.recipe, environment_id=target.environment_id, state=target.state)
        await runtime.endpoint_policy.validate(result.endpoint)
        return result
    finally:
        await close(provider)


async def read(
    runtime: Runtime, selection: Selection, *, credential_cache: ManagedCredentialCache | None = None
) -> DeviceObservation:
    target = selection.target
    assert isinstance(target, Target)
    if isinstance(target.account, ExternalAccount):
        account = target.account
        await runtime.endpoint_policy.validate(account.endpoint)
        connection = HttpEnvdConnectionConfiguration(
            endpoint=account.endpoint,
            request_timeout=runtime.settings.monitoring.request_seconds,
        )
        credential = HttpEnvdCredential(token=SecretStr(account.reveal_token(runtime.keys)))
        async with HttpEnvdProviderRuntime(connection, credential) as carrier:
            return await observe(carrier, account.device_id)
    provider = await open_provider(runtime, target)
    try:
        return await provider.observe_device(
            target.recipe, environment_id=target.environment_id, state=target.state, credential_cache=credential_cache
        )
    finally:
        await close(provider)


def selected_device(runtime: Runtime, device: DeviceRow, source: DataSourceRow) -> Selection:
    config = Configuration.model_validate(source.config)
    assert config.collection == "proxy" and source.enabled
    identity = {
        "source": source_inputs(source, config),
        "device": device.id,
        "device_id": device.device_id,
        "enabled": device.enabled,
        "token_hash": device.token_hash,
    }
    return Selection(
        device.organization_id,
        device.workspace_id,
        device.id,
        source.id,
        source.version,
        hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        device.device_id,
        "waiting",
        ReverseTarget(device.token_hash),
        None if config.export is None else Destination(config.export.endpoint, headers(runtime.keys, source, "export")),
        30 if config.export is None else config.export.interval_seconds,
    )
