"""Typed Environment queries authorize the private target before resolving shared backend credentials."""

from typing import Literal

from a13n_logging import exception_details, get_logger
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError, conflict, disabled, invalid, not_found
from a13n_service.providers.metrics import QueryResult, RangeQuery
from a13n_service.providers.metrics_catalog import MetricQuery, expression
from a13n_service.resources.metrics_data_sources.schemas import Collector, Configuration
from a13n_service.resources.metrics_data_sources.service import query_destination
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.resources.rows import find_row
from a13n_service.runs.environments.monitoring.collection import selected
from a13n_service.runs.environments.monitoring.proxy import LocalResult
from a13n_service.runs.environments.monitoring.pull import Receipt, State, receipt_key
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, allowed_verbs

logger = get_logger(__name__)


class EnvironmentQuery(MetricQuery):
    history: Literal["configured", "temporary"] = "configured"


async def readable_environment(
    session: AsyncSession, actor: Principal, scope: WorkspaceScope, environment_id: str
) -> EnvironmentRow:
    environment = await session.scalar(
        select(EnvironmentRow).where(
            EnvironmentRow.id == environment_id,
            EnvironmentRow.workspace_id == scope.workspace_id,
            EnvironmentRow.organization_id == scope.organization_id,
        )
    )
    if environment is None:
        raise not_found("environment", environment_id)
    if environment.owner_principal_id not in {None, actor.id} and "admin" not in allowed_verbs(actor, scope):
        raise ServiceError("forbidden", "Private environment monitoring is available to its owner", {"verb": "read"})
    return environment


class CollectorStatus(BaseModel):
    collector: Collector | None = None
    state: State | Literal["not_selected", "not_configured", "pending", "failed", "unavailable", "capacity"]
    checked_at: float | None = None


async def collector_status(
    runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str
) -> CollectorStatus:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        environment = await readable_environment(session, actor, scope, environment_id)
        if environment.metrics_data_source_id is None:
            return CollectorStatus(state="not_selected")
        source = await find_row(session, actor, DataSourceRow, scope, environment.metrics_data_source_id, "read")
        try:
            config = Configuration.model_validate(source.config)
        except ValueError:
            return CollectorStatus(state="discovery_failed")
        kind = config.collector
        if kind is None:
            return CollectorStatus(state="not_selected")
        if not source.enabled or not runtime.settings.monitoring.enabled:
            return CollectorStatus(collector=kind, state="disabled")
        if kind not in runtime.settings.monitoring.collectors:
            return CollectorStatus(collector=kind, state="not_configured")
        try:
            selection = await selected(runtime, session, environment, source)
        except Exception as error:
            logger.warning(
                "Collector target selection failed",
                extra={"environment_id": environment_id, "exception_details": exception_details(error)},
            )
            return CollectorStatus(collector=kind, state="discovery_failed")
        if selection.target is None:
            return CollectorStatus(
                collector=kind,
                state="disabled"
                if selection.status == "disabled"
                else ("not_ready" if selection.status == "not_ready" else "unsupported"),
            )
    try:
        value = await runtime.redis.get(receipt_key(kind))
    except RedisError:
        return CollectorStatus(collector=kind, state="unavailable")
    if value is None:
        return CollectorStatus(collector=kind, state="pending")
    receipt = Receipt.model_validate_json(value)
    target = receipt.targets.get(environment_id)
    state = "pending"
    if receipt.state == "failed":
        state = "failed"
    elif target is None and receipt.truncated:
        state = "capacity"
    elif target is not None and target.revision == selection.revision:
        state = target.state
    return CollectorStatus(collector=kind, state=state, checked_at=receipt.checked_at)


async def query_environment(
    runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str, body: EnvironmentQuery
) -> QueryResult | LocalResult:
    local = None
    destination = None
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        environment = await readable_environment(session, actor, scope, environment_id)
        if environment.metrics_data_source_id is None:
            raise conflict("environment", environment_id, "metrics_source_not_selected")
        source = await find_row(session, actor, DataSourceRow, scope, environment.metrics_data_source_id, "read")
        if not source.enabled:
            raise disabled(source.KIND, source.id)
        config = Configuration.model_validate(source.config)
        if body.history == "temporary" or (config.collection == "proxy" and config.query is None):
            if config.collection != "proxy":
                raise conflict("environment", environment_id, "temporary_history_not_configured")
            local = await selected(runtime, session, environment, source)
        else:
            destination = query_destination(runtime.keys, source)
        labels = (
            {"environment_id": environment.id}
            if config.collection in {"proxy", "pull"}
            else {"device_id": environment.device_id or environment.id}
        )
        labels.update(environment.metrics_labels)
        if config.query is not None:
            if any(name in labels and labels[name] != value for name, value in config.query.scope_labels.items()):
                raise invalid("metrics_labels", "selection conflicts with the current source scope labels")
            labels.update(config.query.scope_labels)
        query = RangeQuery(expression=expression(body.metric, labels), start=body.start, end=body.end, step=body.step)
    if local is not None:
        if runtime.metrics_proxy is None:
            raise ServiceError("unavailable", "Proxy monitoring is unavailable")
        return await runtime.metrics_proxy.query(local, body)
    assert destination is not None
    return await runtime.metrics_queries.execute(runtime.endpoint_policy, destination, query)
