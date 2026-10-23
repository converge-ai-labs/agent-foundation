"""Typed monitoring queries for administrator-registered workspace Devices."""

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError, conflict, disabled
from a13n_service.providers.metrics import QueryResult, RangeQuery
from a13n_service.providers.metrics_catalog import expression
from a13n_service.resources.metrics_data_sources.schemas import Configuration
from a13n_service.resources.metrics_data_sources.service import query_destination
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.resources.rows import find_row
from a13n_service.runs.devices.tables import DeviceRow
from a13n_service.runs.environments.monitoring.collection import selected_device
from a13n_service.runs.environments.monitoring.proxy import LocalResult
from a13n_service.runs.environments.monitoring.query import EnvironmentQuery
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def query_device(
    runtime: Runtime, actor: Principal, workspace_id: str, device_id: str, body: EnvironmentQuery
) -> QueryResult | LocalResult:
    local = None
    destination = None
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        device = await find_row(session, actor, DeviceRow, scope, device_id, "read")
        if not device.enabled:
            raise disabled(device.KIND, device.id)
        if device.metrics_data_source_id is None:
            raise conflict(device.KIND, device.id, "metrics_source_not_selected")
        source = await find_row(session, actor, DataSourceRow, scope, device.metrics_data_source_id, "read")
        if not source.enabled:
            raise disabled(source.KIND, source.id)
        config = Configuration.model_validate(source.config)
        if config.collection == "pull":
            raise conflict(device.KIND, device.id, "reverse_devices_require_non_pull_source")
        if body.history == "temporary" or (config.collection == "proxy" and config.query is None):
            if config.collection != "proxy":
                raise conflict(device.KIND, device.id, "temporary_history_not_configured")
            local = selected_device(runtime, device, source)
        else:
            destination = query_destination(runtime.keys, source)
        labels = (
            {"device_registration_id": device.id} if config.collection == "proxy" else {"device_id": device.device_id}
        )
        if config.query is not None:
            if any(name in labels and labels[name] != value for name, value in config.query.scope_labels.items()):
                raise conflict(device.KIND, device.id, "metrics_scope_conflict")
            labels.update(config.query.scope_labels)
        query = RangeQuery(expression=expression(body.metric, labels), start=body.start, end=body.end, step=body.step)
    if local is not None:
        if runtime.metrics_proxy is None:
            raise ServiceError("unavailable", "Proxy monitoring is unavailable")
        return await runtime.metrics_proxy.query(local, body)
    assert destination is not None
    return await runtime.metrics_queries.execute(runtime.endpoint_policy, destination, query)
