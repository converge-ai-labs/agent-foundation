"""Validate source selection inside the Environment mutation's transaction."""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import assign
from a13n_service.infra.errors import disabled, invalid
from a13n_service.infra.ids import ObjectId
from a13n_service.resources.metrics_data_sources.schemas import Configuration, Labels
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.resources.rows import find_row
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, authorize


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics_data_source_id: ObjectId | None
    metrics_labels: Labels = Field(default_factory=dict)


async def select_source(
    session: AsyncSession, actor: Principal, environment: EnvironmentRow, source_id: str | None, labels: dict[str, str]
) -> bool:
    scope = WorkspaceScope(environment.organization_id, environment.workspace_id)
    if source_id is not None:
        authorize(actor, scope, "write")
        source = await find_row(session, actor, DataSourceRow, scope, source_id, "read")
        if not source.enabled:
            raise disabled(source.KIND, source.id)
        if environment.template_id is None and not source.builtin:
            # External endpoints self-report Device identity. Only an administrator
            # may associate that identity with shared backend history. Apply this to
            # every custom source, including ones that gain query capability later.
            authorize(actor, scope, "admin")
        config = Configuration.model_validate(source.config)
        if config.query is not None and any(
            name in config.query.scope_labels and config.query.scope_labels[name] != value
            for name, value in labels.items()
        ):
            raise invalid("metrics_labels", "selection cannot override source scope labels")
    elif labels:
        raise invalid("metrics_labels", "metric identity requires a data source")
    if source_id is not None and (
        labels != (environment.metrics_labels or {}) or (labels and source_id != environment.metrics_data_source_id)
    ):
        # Replacing or clearing a mapping is administrative; retaining the same
        # binding is not a relabel. Ordinary managers may still remove a binding.
        authorize(actor, scope, "admin")
    return bool(assign(environment, {"metrics_data_source_id": source_id, "metrics_labels": labels}))
