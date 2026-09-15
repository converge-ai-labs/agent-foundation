"""Migrated terminal guards allow labels while preserving sealed execution."""

import asyncio

import pytest
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.gateway.labels import InteractionLabels
from a13n_service.interactions.models import RunRecord
from a13n_service.labels import LabelsBody, labels_etag
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.exc import DBAPIError

from tests.hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import NOW, _seed_interaction_database


@pytest.mark.anyio
async def test_terminal_label_edits_do_not_weaken_execution_guards(postgres_database):
    config = postgres_database
    migrator = DatabaseMigrator(config)
    await asyncio.to_thread(migrator.upgrade)
    engine = create_sql_engine(config)
    sessions = create_session_factory(engine)
    try:
        await _seed_interaction_database(sessions)
        await seed_run_and_secret(sessions)
        await seed_hook_actor_access(sessions)
        async with transaction(sessions) as database:
            row = await database.get(RunRecord, RUN_ID)
            row.status = "failed"
            row.failure_json = {"code": "test", "message": "failed", "retry_hint": "none", "details": {}}
            row.sealed_at = NOW
        service = InteractionLabels(sessions)
        body, etag = await service.put_run_labels(
            actor=hook_actor(),
            run_id=RUN_ID,
            body=LabelsBody(labels={"terminal": "editable"}),
            if_match=labels_etag(RUN_ID, {}),
        )
        assert body.labels == {"terminal": "editable"}
        with pytest.raises(DBAPIError, match="immutable"):
            async with transaction(sessions) as database:
                row = await database.get(RunRecord, RUN_ID)
                row.labels = {"terminal": "should-rollback"}
                row.priority += 1
        read, current = await service.get_run_labels(actor=hook_actor(), run_id=RUN_ID)
        assert read == body and current == etag
    finally:
        await engine.dispose()
