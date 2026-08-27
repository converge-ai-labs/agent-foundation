import pytest
from a13n_service.database.config import MigrationConfig
from pydantic import ValidationError


def test_migration_config_rejects_unbounded_timeouts() -> None:
    with pytest.raises(ValidationError):
        MigrationConfig(statement_timeout_seconds=0)


def test_migration_config_is_frozen() -> None:
    config = MigrationConfig()

    with pytest.raises(ValidationError):
        config.statement_timeout_seconds = 1
