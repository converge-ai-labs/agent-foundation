"""Canonical metadata registry for every a13n Service ORM model."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for service-owned models included in the migration history."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def service_metadata() -> MetaData:
    """Return the complete, explicitly imported service metadata registry."""

    # The distribution descriptor imports every service-owned domain explicitly.
    # Deliberately avoid module scanning or plugin discovery.
    from a13n_service.agents import models as agent_models
    from a13n_service.assets import models as asset_models
    from a13n_service.connectivity.accounts import models as account_models
    from a13n_service.connectivity.accounts import target_models
    from a13n_service.connectivity.connectors import models as connector_models
    from a13n_service.connectivity.ingress import admission_models as ingress_admission_models
    from a13n_service.connectivity.mcp import models as mcp_models
    from a13n_service.durable_operations import models as durable_operations_models
    from a13n_service.environments import models as environment_models
    from a13n_service.gateway import models as gateway_models
    from a13n_service.hooks import models as hook_models
    from a13n_service.iam import models as iam_models
    from a13n_service.interactions import control_models as interaction_control_models
    from a13n_service.interactions import models as interaction_models
    from a13n_service.lifecycle import models as lifecycle_models
    from a13n_service.models import models as model_models
    from a13n_service.object_retention import models as object_retention_models
    from a13n_service.search import models as search_models
    from a13n_service.secrets import models as secret_models
    from a13n_service.skills import models as skill_models
    from a13n_service.subagents import models as subagent_models

    del (
        agent_models,
        asset_models,
        account_models,
        connector_models,
        durable_operations_models,
        environment_models,
        gateway_models,
        hook_models,
        iam_models,
        interaction_control_models,
        interaction_models,
        ingress_admission_models,
        target_models,
        lifecycle_models,
        mcp_models,
        model_models,
        object_retention_models,
        search_models,
        secret_models,
        skill_models,
        subagent_models,
    )
    return Base.metadata
