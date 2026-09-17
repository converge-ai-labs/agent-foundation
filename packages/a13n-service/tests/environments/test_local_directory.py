import pytest
from a13n_service.environments.local_directory import ManagedLocalDirectory

pytestmark = pytest.mark.anyio


async def test_owned_directories_are_independent_and_deletion_is_idempotent(tmp_path):
    first = ManagedLocalDirectory(tmp_path, "env_first")
    second = ManagedLocalDirectory(tmp_path, "env_second")
    await first.create()
    await second.create()
    (first.path / "result").write_text("first")
    (second.path / "result").write_text("second")
    await first.create()
    assert (first.path / "result").read_text() == "first"
    await first.delete()
    await first.delete()
    assert not first.path.exists()
    assert (second.path / "result").read_text() == "second"
    assert tmp_path.exists()


@pytest.mark.parametrize("replacement", ["environments", "env_first"])
async def test_rejects_symlink_directory_replacement(tmp_path, replacement):
    directory = ManagedLocalDirectory(tmp_path / "base", "env_first")
    await directory.create()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("important")
    directory.path.rmdir()
    target = directory.path
    if replacement == "environments":
        target = target.parent
        target.rmdir()
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        await directory.create()
    with pytest.raises(OSError):
        await directory.delete()
    assert (outside / "keep").read_text() == "important"


async def test_service_allocates_distinct_manual_environments_and_cleans_only_owned_data(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path
):
    from datetime import UTC, datetime

    from a13n_service.environments.configuration import load_configuration
    from a13n_service.environments.domain import (
        CreateManagedEnvironmentRequest,
        CreateProviderRequest,
        CreateTemplateRequest,
        EnvironmentCommandRequest,
    )
    from a13n_service.environments.lifecycle import EnvironmentLifecycle
    from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentRecord
    from a13n_service.storage import short_session, transaction

    from .conftest import WORKSPACE_ID, actor

    now = datetime(2026, 9, 17, tzinfo=UTC)
    provider = await environment_service.create_provider(
        actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="a13n.direct-local", name="Local")
    )
    template = await environment_service.create_template(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="local-template",
        request=CreateTemplateRequest(
            name="Local",
            provider_id=provider.id,
            configuration={"root": {"path": str(tmp_path)}},
            retention={"idle": {"stop_after": None, "delete_after": None}},
        ),
    )
    first, second = [
        await environment_service.create_environment(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key=key,
            request=CreateManagedEnvironmentRequest(template_id=template.id),
        )
        for key in ("first", "second")
    ]
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    for environment in (first, second):
        async with transaction(environment_sessions) as session:
            row = await session.get(EnvironmentRecord, environment.id)
            backend = await session.get(EnvironmentProviderRecord, provider.id)
            config = await load_configuration(session, row)
            operation = lifecycle._claim(row, backend, config, "prepare", now)
        result = await lifecycle.execute(operation)
        await result.environment.operations.files.write_text("/proof", environment.id, mode="create")
        await result.environment.close()
    async with short_session(environment_sessions) as session:
        rows = [await session.get(EnvironmentRecord, environment.id) for environment in (first, second)]
        assert rows[0].target_identity != rows[1].target_identity
        assert all(row.status == "running" for row in rows)
    for action in ("stop", "delete"):
        await environment_service.request_command(
            actor=actor(),
            environment_id=first.id,
            idempotency_key=action,
            request=EnvironmentCommandRequest(action=action),
        )
        await lifecycle.maintain(first.id)
        if action == "stop":
            assert (tmp_path / "environments" / first.id / "proof").read_text() == first.id
    assert not (tmp_path / "environments" / first.id).exists()
    assert (tmp_path / "environments" / second.id / "proof").read_text() == second.id
    assert tmp_path.is_dir()
