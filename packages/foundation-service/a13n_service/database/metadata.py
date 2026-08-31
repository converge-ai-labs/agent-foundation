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
    from a13n_service.iam import models as iam_models
    from a13n_service.model_management import models as model_management_models
    from a13n_service.secret_management import models as secret_management_models
    from a13n_service.skill_management import models as skill_management_models

    del iam_models, model_management_models, secret_management_models, skill_management_models
    return Base.metadata
