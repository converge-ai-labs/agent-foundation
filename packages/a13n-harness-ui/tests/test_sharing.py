"""Persisted grants narrow browser access, without changing Agent authority."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.share_models import ShareCreate
from a13n_harness_ui.storage.database import short_session, transaction
from a13n_harness_ui.storage.models import ShareGrantRecord
from a13n_harness_ui.surfaces import NewThreadDefaults

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


async def test_share_survives_restart_without_storing_secret_and_revoke_is_terminal(tmp_path):
    path = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=path, instrumentation=None) as app:
        thread = await app.create_thread(title="Design review")
        created = await app.sharing.create(ShareCreate(scope="thread", target_id=thread.thread_id))
        share_id = created.grant.share_id
        assert len(created.key) >= 40
        async with short_session(app._store.database.sessions) as session:
            record = await session.get(ShareGrantRecord, share_id)
            assert record is not None
            assert record.token_digest != created.key and len(record.token_digest) == 64
        assert created.key not in repr(created)
        assert created.key not in str(await app.sharing.list("thread", thread.thread_id))
        with pytest.raises(HarnessUiError, match="unavailable"):
            await app.sharing.authenticate(share_id, "incorrect")
    async with open_harness_ui_app(settings, configuration_path=path, instrumentation=None) as app:
        access = await app.sharing.authenticate(share_id, created.key)
        assert access.title == "Design review"
        assert access.grant.permission == "view"
        await app.sharing.update(share_id, "prompt")
        assert (await app.sharing.authenticate(share_id, created.key)).grant.permission == "prompt"
        await app.sharing.revoke(share_id)
        await app.sharing.revoke(share_id)
        with pytest.raises(HarnessUiError):
            await app.sharing.authenticate(share_id, created.key)
        with pytest.raises(HarnessUiError):
            await app.sharing.update(share_id, "view")
        assert await app.sharing.list("thread", thread.thread_id) == ()


async def test_thread_scope_rejects_other_threads_and_view_cannot_prompt(tmp_path):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path, instrumentation=None) as app:
        first = await app.create_thread()
        other = await app.create_thread()
        created = await app.sharing.create(ShareCreate(scope="thread", target_id=first.thread_id))
        share_id = created.grant.share_id
        detail = await app.sharing.thread(share_id, first.thread_id)
        assert "run" not in detail.available_actions
        with pytest.raises(HarnessUiError) as rejected:
            await app.sharing.thread(share_id, other.thread_id)
        assert rejected.value.code == "share_unavailable"
        with pytest.raises(HarnessUiError) as rejected:
            await app.sharing.thread(share_id, first.thread_id, prompt=True)
        assert rejected.value.code == "share_forbidden"
        await app.sharing.update(share_id, "prompt")
        assert "run" in (await app.sharing.thread(share_id, first.thread_id, prompt=True)).available_actions
        with pytest.raises(HarnessUiError):
            await app.sharing.create_thread(share_id, thread_id=f"thread_{uuid4().hex}", title=None)


async def test_project_share_includes_future_conversations_and_uses_project_defaults(tmp_path):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path, instrumentation=None) as app:
        created = await app.sharing.create(ShareCreate(scope="project", target_id="project-main", permission="prompt"))
        share_id = created.grant.share_id
        future = await app.create_thread()
        assert (await app.sharing.thread(share_id, future.thread_id)).thread.thread_id == future.thread_id
        guest = await app.sharing.create_thread(share_id, thread_id=f"thread_{uuid4().hex}", title="Guest idea")
        assert guest.configuration.project_id == "project-main"
        assert guest.configuration.agent_source.id == "agent-assistant"
        unrelated = await app.create_thread(defaults=NewThreadDefaults(project_id=None))
        with pytest.raises(HarnessUiError):
            await app.sharing.thread(share_id, unrelated.thread_id)
        await app.sharing.update(share_id, "view")
        with pytest.raises(HarnessUiError):
            await app.sharing.create_thread(share_id, thread_id=f"thread_{uuid4().hex}", title=None)


async def test_expiry_and_missing_target_stop_access(tmp_path):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path, instrumentation=None) as app:
        with pytest.raises(HarnessUiError):
            await app.sharing.create(ShareCreate(scope="project", target_id="project-missing"))
        created = await app.sharing.create(ShareCreate(scope="project", target_id="project-main"))
        async with transaction(app._store.database.sessions) as session:
            record = await session.get(ShareGrantRecord, created.grant.share_id)
            assert record is not None
            record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(HarnessUiError):
            await app.sharing.authenticate(created.grant.share_id, created.key)
        assert await app.sharing.list("project", "project-main") == ()
