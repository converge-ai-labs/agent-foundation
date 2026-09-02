"""Canonical metadata registry for every Foundation Service ORM model."""

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
    from a13n_service.agent_presets import models as agent_preset_models
    from a13n_service.assets import models as asset_models
    from a13n_service.durable_operations import models as durable_operations_models
    from a13n_service.environments import models as environment_models
    from a13n_service.iam import models as iam_models
    from a13n_service.model_configs import models as model_config_models
    from a13n_service.plugins import models as plugin_models
    from a13n_service.secrets import models as secret_models
    from a13n_service.skills import models as skill_models

    del (
        agent_preset_models,
        asset_models,
        durable_operations_models,
        environment_models,
        iam_models,
        model_config_models,
        plugin_models,
        secret_models,
        skill_models,
    )
    return Base.metadata
