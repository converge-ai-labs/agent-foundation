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
from .test_coordinators import coordinator_configuration

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def owned_child(app, root_id, *, title="Async child"):
    root = await app._store.threads.get(root_id)
    assert root is not None
    return await app._store.threads.create(
        thread_id=f"thread_{uuid4().hex}",
        parent_thread_id=root_id,
        configuration=root.configuration,
        initial_state=root.initial_state,
        title=title,
    )


async def test_thread_share_follows_promotion_and_future_worker_lineage(tmp_path):
    path = coordinator_configuration(tmp_path)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    defaults = NewThreadDefaults(project_id="project-main")
    async with open_harness_ui_app(settings, configuration_path=path, host_mode="webui", instrumentation=None) as app:
        lead = await app.create_thread(defaults=defaults)
        independent = await app.create_thread(defaults=defaults)
        chat = await app.sharing.create(ShareCreate(scope="thread", target_id=lead.thread_id))
        share_id = chat.grant.share_id
        assert (await app.sharing.authenticate(share_id, chat.key)).target_role == "ordinary"
        await app.promote_coordinator(lead.thread_id)
        worker = await app.create_thread(defaults=defaults, coordinator_thread_id=lead.thread_id)
        child = await owned_child(app, worker.thread_id)
        grandchild = await owned_child(app, child.thread_id)
        assert (await app.sharing.authenticate(share_id, chat.key)).target_role == "coordinator"
        assert (await app.sharing.thread(share_id, lead.thread_id)).thread.unarchived_worker_count == 1
        assert (await app.sharing.thread(share_id, worker.thread_id)).thread.coordinator_thread_id == lead.thread_id
        assert (await app.sharing.thread(share_id, grandchild.thread_id)).thread.parent_thread_id == child.thread_id
        with pytest.raises(HarnessUiError, match="unavailable"):
            await app.sharing.thread(share_id, independent.thread_id)


async def test_direct_worker_share_hides_owner_and_excludes_siblings(tmp_path):
    path = coordinator_configuration(tmp_path)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    defaults = NewThreadDefaults(project_id="project-main")
    async with open_harness_ui_app(settings, configuration_path=path, host_mode="webui", instrumentation=None) as app:
        lead = await app.create_thread(defaults=defaults, coordinator=True)
        worker = await app.create_thread(defaults=defaults, coordinator_thread_id=lead.thread_id)
        sibling = await app.create_thread(defaults=defaults, coordinator_thread_id=lead.thread_id)
        child = await owned_child(app, worker.thread_id)
        chat = await app.sharing.create(ShareCreate(scope="thread", target_id=worker.thread_id))
        share_id = chat.grant.share_id
        assert (await app.sharing.authenticate(share_id, chat.key)).target_role == "worker"
        assert (await app.sharing.thread(share_id, worker.thread_id)).thread.coordinator_thread_id is None
        assert (await app.sharing.thread(share_id, child.thread_id)).thread.parent_thread_id == worker.thread_id
        for thread_id in (lead.thread_id, sibling.thread_id):
            with pytest.raises(HarnessUiError, match="unavailable"):
                await app.sharing.thread(share_id, thread_id)


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


async def test_sharing_thread_model_display_is_opt_in_and_summarizes_native_and_acp(tmp_path):
    from a13n_harness_ui.acp_sessions import AcpSessionState
    from a13n_harness_ui.configuration.models import AcpAgentResource

    path = _write_configuration(tmp_path)
    acp_dir = tmp_path / "acp_agents"
    acp_dir.mkdir(exist_ok=True)
    (acp_dir / "claude.yaml").write_text(
        AcpAgentResource(
            schema_version="1",
            kind="acp_agent",
            id="acp-claude",
            name="Claude Code",
            preset="claude_code",
            command="claude",
        ).model_dump_json(exclude_none=True),
        encoding="utf-8",
    )
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=path, host_mode="webui", instrumentation=None) as app:
        native = await app.create_thread(title="Native thread")
        acp = await app.create_thread(defaults=NewThreadDefaults(agent_id="acp-claude"), title="ACP thread")
        await app._store.threads.save_acp_state(
            acp.thread_id,
            AcpSessionState(
                digest="digest-1",
                options={"model": "sonnet", "thought_level": "high"},
            ).model_dump_json(),
        )
        share = await app.sharing.create(ShareCreate(scope="project", target_id="project-main"))
        share_id = share.grant.share_id

        # Default authorization checks stay cheap and omit display enrichment.
        assert (await app.sharing.thread(share_id, native.thread_id)).model_display is None

        native_detail = await app.sharing.thread(share_id, native.thread_id, include_display=True)
        assert native_detail.model_display is not None
        assert native_detail.model_display.agent_name == "Assistant"
        assert native_detail.model_display.model_name == "Primary"
        assert native_detail.model_display.model_identity == "openai:gpt-5"
        assert native_detail.model_display.thinking_summary is None
        assert native_detail.model_display.fast == "default"

        acp_detail = await app.sharing.thread(share_id, acp.thread_id, include_display=True)
        assert acp_detail.model_display is not None
        assert acp_detail.model_display.agent_name == "Claude Code"
        assert acp_detail.model_display.model_name == "sonnet"
        assert acp_detail.model_display.model_identity == "claude_code"
        assert acp_detail.model_display.thinking_summary == "high"
        assert acp_detail.model_display.fast == "default"
