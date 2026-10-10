"""Old plain-JSON displays and checkpoints upgrade without losing history or weakening sealed-run guards."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from a13n_service.app import build_app
from a13n_service.cli import main
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbox import OutboxRow, prepare
from a13n_service.migrations import run_objects
from a13n_service.runs import checkpoints
from a13n_service.runs.checkpoints import StatePointer, TailPointer
from a13n_service.runs.tables import RunRow
from anyio.to_thread import run_sync
from click.testing import CliRunner
from pydantic import ValidationError
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.anyio

LEGACY = {
    "items": [
        {
            "id": "itm_old",
            "kind": "text_message",
            "state": "completed",
            "first_stream_id": "1-1",
            "last_stream_id": "1-3",
            "started_at": "2026-10-01T00:00:00Z",
            "ended_at": "2026-10-01T00:00:01Z",
            "content": {"text": "原始消息🙂", "role": "assistant", "metadata": {"nested": [True, 1, None]}},
        }
    ],
    "position": {"attempt": 1, "sequence": 3},
    "resume_after": "123-4",
    "dropped": 0,
}


def pointer(data: dict) -> run_objects.LegacyPointer:
    return run_objects.LegacyPointer(
        key="orgs/org_test/runs/run_test/display/1/original",
        digest="0" * 64,
        size=1,
        format=1,
        position=data["position"],
    )


@pytest.mark.parametrize("items", [[], LEGACY["items"]])
async def test_legacy_display_keeps_content_order_position_and_resume_hint(items):
    old = {**deepcopy(LEGACY), "items": items}
    result = run_objects.convert_display(json.dumps(old).encode(), pointer(old))
    assert result.first == 1
    assert [item.ordinal for item in result.items] == list(range(1, len(items) + 1))
    assert [item.content for item in result.items] == [item["content"] for item in items]
    assert str(result.position) == "1-3" and result.resume_after == "123-4"


@pytest.mark.parametrize("change", ["dropped", "position", "ordinal", "duplicate", "unknown"])
async def test_ambiguous_or_truncated_display_is_refused(change):
    old = deepcopy(LEGACY)
    original_pointer = pointer(old)
    if change == "dropped":
        old["dropped"] = 3
    elif change == "position":
        old["position"]["sequence"] += 1
    elif change == "ordinal":
        old["items"][0]["ordinal"] = 99
    elif change == "duplicate":
        old["items"] *= 2
    else:
        old["unknown"] = "refuse"
    with pytest.raises(ValueError):
        run_objects.convert_display(json.dumps(old).encode(), original_pointer)


@pytest.fixture
async def legacy_run(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    started = await runs_kit.start_thread(service, agent, "remember seven")
    run_id = started["run"]["id"]
    scripted_model.say("Noted")
    await (await runs_kit.attempt(service))
    async with short_session(service.runtime.storage) as session:
        row = await session.get(RunRow, run_id)
        tail_pointer = TailPointer.model_validate(row.tail)
        state_pointer = StatePointer.model_validate(row.checkpoint)
        organization_id = row.organization_id
    tail = await checkpoints.load_tail(service.runtime.objects, tail_pointer)
    state_bytes = await checkpoints.load(service.runtime.objects, state_pointer)
    old_display = {
        "items": [item.model_dump(mode="json", exclude={"ordinal", "content_refs"}) for item in tail.items],
        "position": tail.position.model_dump(),
        "resume_after": tail.resume_after,
        "dropped": 0,
    }
    display_bytes = json.dumps(old_display, ensure_ascii=False).encode()
    base = checkpoints.run_prefix(organization_id, run_id)
    display_ref = await service.runtime.objects.put(
        f"{base}/display/1/legacy", display_bytes, content_type="application/json"
    )
    state_ref = await service.runtime.objects.put(
        f"{base}/state/1/legacy", state_bytes, content_type="application/json"
    )
    old_tail = {
        "key": display_ref.key,
        "digest": display_ref.digest,
        "size": display_ref.size,
        "format": 1,
        "position": old_display["position"],
    }
    old_state = {
        "key": state_ref.key,
        "digest": state_ref.digest,
        "size": state_ref.size,
        "format": 1,
        "seq": state_pointer.seq,
        "attempt": state_pointer.attempt,
    }
    # Install the pre-pagination persistence shape, as the column-rename migration leaves it. Its old cleanup
    # deliveries have already been retained and purged in the source deployment.
    async with transaction(service.runtime.storage) as session:
        await session.execute(delete(OutboxRow).where(OutboxRow.kind == "checkpoint_cleanup"))
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await session.execute(text("ALTER TABLE runs DISABLE TRIGGER guard_run"))
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(tail=old_tail, checkpoint=old_state))
        await session.execute(text("ALTER TABLE runs ENABLE TRIGGER guard_run"))
    return {
        "id": run_id,
        "agent": agent,
        "started": started,
        "tail": old_tail,
        "state": old_state,
        "display_bytes": display_bytes,
        "state_bytes": state_bytes,
    }


async def pointers(service, run_id):
    async with short_session(service.runtime.storage) as session:
        row = await session.get(RunRow, run_id)
        return row.tail, row.checkpoint


async def test_conversion_restores_run_and_items_apis_preserves_state_and_is_repeatable(
    service, legacy_run, scripted_model, runs_kit, tmp_path: Path, caplog
):
    runtime = service.runtime
    run_id = legacy_run["id"]
    backup = tmp_path / "backup"
    caplog.set_level("INFO", logger="a13n_service.migrations.run_objects")
    before = await runtime.objects.keys("orgs", limit=1000)
    with pytest.raises(RuntimeError, match="migrate-run-objects"):
        await run_objects.require_current(runtime.storage)
    with pytest.raises(ValidationError):
        await runs_kit.get_run(service, run_id)
    assert await run_objects.migrate(runtime.storage, runtime.objects, backup_dir=backup) == 1
    assert not backup.exists() and await runtime.objects.keys("orgs", limit=1000) == before
    assert await pointers(service, run_id) == (legacy_run["tail"], legacy_run["state"])

    assert await run_objects.migrate(runtime.storage, runtime.objects, backup_dir=backup, apply=True) == 1
    tail, state = await pointers(service, run_id)
    assert tail["key"] != legacy_run["tail"]["key"] and state["key"] != legacy_run["state"]["key"]
    assert await checkpoints.load(runtime.objects, StatePointer.model_validate(state)) == legacy_run["state_bytes"]
    assert await runtime.objects.get(legacy_run["tail"]["key"]) == legacy_run["display_bytes"]
    assert await runtime.objects.get(legacy_run["state"]["key"]) == legacy_run["state_bytes"]
    (saved,) = backup.iterdir()
    assert saved.stat().st_mode & 0o777 == 0o700
    assert (saved / "display.bin").stat().st_mode & 0o777 == 0o600
    assert (saved / "display.bin").read_bytes() == legacy_run["display_bytes"]
    assert (saved / "checkpoint.bin").read_bytes() == legacy_run["state_bytes"]
    assert json.loads((saved / "row.json").read_text())["tail"] == legacy_run["tail"]
    after = await runtime.objects.keys("orgs", limit=1000)
    assert await run_objects.migrate(runtime.storage, runtime.objects, backup_dir=backup, apply=True) == 0
    assert await runtime.objects.keys("orgs", limit=1000) == after
    await run_objects.require_current(runtime.storage)
    assert (await runs_kit.get_run(service, run_id))["status"] == "completed"
    thread_id = legacy_run["started"]["thread"]["id"]
    listing = await service.client.get(f"{service.api}/threads/{thread_id}/runs")
    assert listing.status_code == 200 and listing.json()["items"][0]["id"] == run_id
    items = await runs_kit.items(service, run_id)
    old_items = json.loads(legacy_run["display_bytes"])["items"]
    assert [item["content"] for item in items["items"]] == [item["content"] for item in old_items]
    assert [item["id"] for item in items["items"]] == [item["id"] for item in old_items]
    assert [item["ordinal"] for item in items["items"]] == list(range(1, len(old_items) + 1))
    page = await service.client.get(f"{service.api}/runs/{run_id}/items?after=1&limit=1")
    assert page.status_code == 200 and [item["ordinal"] for item in page.json()["items"]] == [2]
    assert any(record.message == "Legacy run objects converted" for record in caplog.records)

    # Reading a repaired transcript is not enough: its formerly uncompressed checkpoint must continue too.
    scripted_model.say("Seven")
    next_run = await runs_kit.submit(service, thread_id, runs_kit.message(legacy_run["agent"], "what was it?"))
    assert next_run.status_code == 201, next_run.text
    await (await runs_kit.attempt(service))
    result = await runs_kit.get_run(service, next_run.json()["run"]["id"])
    assert result["status"] == "completed" and result["output"] == "Seven"
    await scripted_model.request()
    continued = await scripted_model.request()
    assert "remember seven" in json.dumps(continued["messages"])


@pytest.mark.parametrize("failure", ["missing", "digest", "upload", "database"])
async def test_failure_keeps_original_pointers_and_guard(service, legacy_run, tmp_path, monkeypatch, failure):
    runtime = service.runtime
    run_id = legacy_run["id"]
    if failure == "missing":
        await runtime.objects.delete(legacy_run["tail"]["key"])
    elif failure == "digest":
        original = runtime.objects.get

        async def damaged(key):
            return b"wrong bytes" if key == legacy_run["tail"]["key"] else await original(key)

        monkeypatch.setattr(runtime.objects, "get", damaged)
    elif failure == "upload":
        original = runtime.objects.put

        async def fail_state(key, data, *, content_type):
            if "/state/" in key:
                raise OSError("upload failed")
            return await original(key, data, content_type=content_type)

        monkeypatch.setattr(runtime.objects, "put", fail_state)
    else:
        async with transaction(runtime.storage) as session:
            await session.execute(
                text("""
                CREATE FUNCTION refuse_test_update() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'simulated update failure'; END $$
            """)
            )
            await session.execute(
                text(
                    "CREATE TRIGGER refuse_test_update BEFORE UPDATE ON runs FOR EACH ROW EXECUTE FUNCTION refuse_test_update()"
                )
            )
    expected = {"missing": ServiceError, "digest": ServiceError, "upload": OSError, "database": DBAPIError}[failure]
    with pytest.raises(expected):
        await run_objects.migrate(runtime.storage, runtime.objects, backup_dir=tmp_path / "backup", apply=True)
    assert await pointers(service, run_id) == (legacy_run["tail"], legacy_run["state"])
    async with short_session(runtime.storage) as session:
        assert (
            await session.scalar(
                text("SELECT tgenabled FROM pg_trigger WHERE tgrelid='runs'::regclass AND tgname='guard_run'")
            )
            == "O"
        )
    if failure != "database":
        with pytest.raises(DBAPIError, match="sealed run facts are immutable"):
            async with transaction(runtime.storage) as session:
                await session.execute(update(RunRow).where(RunRow.id == run_id).values(tail=None))


@pytest.mark.parametrize("status", ["pending", "dead"])
async def test_unfinished_cleanup_cannot_delete_the_replacements(service, legacy_run, tmp_path, status):
    runtime = service.runtime
    tenant = service.tenant
    async with transaction(runtime.storage) as session:
        session.add(
            prepare(
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                kind="checkpoint_cleanup",
                target={"run_id": legacy_run["id"]},
                payload={"keep": [legacy_run["tail"]["key"]], "before_attempt": None, "after": None},
                dead="old_failure" if status == "dead" else None,
            )
        )
    with pytest.raises(RuntimeError, match="unfinished checkpoint cleanup"):
        await run_objects.migrate(runtime.storage, runtime.objects, backup_dir=tmp_path / "backup", apply=True)
    assert not (tmp_path / "backup").exists()
    assert await pointers(service, legacy_run["id"]) == (legacy_run["tail"], legacy_run["state"])


async def test_active_work_refuses_conversion(service, legacy_run, runs_kit, tmp_path):
    await runs_kit.start_thread(service, legacy_run["agent"], "still pending")
    with pytest.raises(RuntimeError, match="no accepted or running runs"):
        await run_objects.migrate(
            service.runtime.storage, service.runtime.objects, backup_dir=tmp_path / "backup", apply=True
        )
    assert not (tmp_path / "backup").exists()


async def test_startup_and_migration_check_refuse_legacy_data_until_operator_conversion(
    service, legacy_run, tmp_path, clean_environment
):
    runtime = service.runtime
    app = build_app(role="worker", settings=runtime.settings)
    with pytest.raises(RuntimeError, match="migrate-run-objects"):
        async with app.router.lifespan_context(app):
            pytest.fail("Worker must not start against unreadable legacy run objects")
    config = tmp_path / "service.toml"
    config.write_text(
        f'[database]\nurl = "{runtime.settings.database.url.get_secret_value()}"\nauto_migrate = false\n'
        f'[objects]\nroot = "{runtime.settings.objects.root}"\n'
    )

    async def invoke(*args):
        return await run_sync(lambda: CliRunner().invoke(main, ["--config", str(config), *args]))

    check = await invoke("migrate", "--check")
    assert check.exit_code == 1 and "migrate-run-objects" in check.output
    missing_backup = await invoke("migrate-run-objects", "--apply")
    assert missing_backup.exit_code == 2 and "--backup-dir" in missing_backup.output
    dry_run = await invoke("migrate-run-objects")
    assert dry_run.exit_code == 0 and json.loads(dry_run.output.splitlines()[-1])["verified"] == 1
    converted = await invoke("migrate-run-objects", "--apply", "--backup-dir", str(tmp_path / "backup"))
    assert converted.exit_code == 0, converted.output
    assert json.loads(converted.output.splitlines()[-1])["converted"] == 1
    check = await invoke("migrate", "--check")
    assert check.exit_code == 0, check.output


async def test_concurrent_row_change_refuses_to_replace_stale_pointers(service, legacy_run, tmp_path, monkeypatch):
    original = run_objects._publish
    runtime = service.runtime
    changed = False

    async def change_during_upload(objects, source, kind, attempt, data):
        nonlocal changed
        if not changed:
            changed = True
            async with transaction(runtime.storage) as session:
                await session.execute(update(RunRow).where(RunRow.id == source.id).values(labels={"edited": "yes"}))
        return await original(objects, source, kind, attempt, data)

    monkeypatch.setattr(run_objects, "_publish", change_during_upload)
    with pytest.raises(RuntimeError, match="changed during conversion"):
        await run_objects.migrate(runtime.storage, runtime.objects, backup_dir=tmp_path / "backup", apply=True)
    assert await pointers(service, legacy_run["id"]) == (legacy_run["tail"], legacy_run["state"])
    async with short_session(runtime.storage) as session:
        assert (await session.get(RunRow, legacy_run["id"])).labels == {"edited": "yes"}
