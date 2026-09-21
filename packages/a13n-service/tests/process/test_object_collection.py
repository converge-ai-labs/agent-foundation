from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
import pytest
from a13n_service.application_errors import ApplicationError
from a13n_service.object_retention.collector import ObjectCollector
from a13n_service.object_retention.models import ObjectPublicationRecord
from a13n_service.object_retention.persistence import require_object_publications
from a13n_service.object_retention.publication import PublicationObjectStore
from a13n_service.skills.models import SkillUploadRecord
from a13n_service.skills.package import skill_package_object_key
from a13n_service.storage import ObjectConflict, ObjectNotFound, ObjectStoreUnavailable, short_session, transaction
from a13n_service.temporal import utc_now

from tests.hooks.support import RUN_ID, seed_run_and_secret
from tests.interactions.conftest import ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from tests.sql_capture import capture_sql

from .test_collection import collection_sessions as collection_sessions

pytestmark = pytest.mark.anyio


@dataclass
class _Clock:
    now: datetime

    def __call__(self):
        return self.now


def _collector(sessions, objects, clock):
    return ObjectCollector(
        sessions, objects, minimum_age=timedelta(hours=24), batch_limit=16, item_timeout_seconds=30, clock=clock
    )


def _upload(key_digest, now):
    return SkillUploadRecord(
        id="su_aaaaaaaaaaaaaaaa",
        organization_id=ORGANIZATION_ID,
        workspace_id=WORKSPACE_ID,
        uploader_type="user",
        uploader_id=USER_ID,
        archive_sha256="a" * 64,
        manifest={"content_digest": key_digest},
        created_at=now,
        expires_at=now + timedelta(days=1),
    )


@pytest.mark.parametrize(
    ("suffix", "registered"),
    [
        ("state.json", False),
        ("display_messages.json", True),
        ("payloads/input/" + "a" * 64 + ".json", True),
        ("nested/state.json", True),
    ],
)
async def test_only_exact_run_state_creation_skips_publication_registration(
    collection_sessions, object_store, suffix, registered
):
    sessions = collection_sessions
    key = f"organizations/{ORGANIZATION_ID}/runs/run_new/{suffix}"
    writer = PublicationObjectStore(object_store, sessions)
    with capture_sql(sessions) as statements:
        await writer.put(key, b"initial", if_none_match=True)
    assert bool(statements) is registered
    async with short_session(sessions) as database:
        assert (await database.get(ObjectPublicationRecord, key) is not None) is registered


async def test_run_state_writes_preserve_create_only_and_version_conditions(collection_sessions, object_store):
    sessions = collection_sessions
    key = f"organizations/{ORGANIZATION_ID}/runs/run_new/state.json"
    writer = PublicationObjectStore(object_store, sessions)
    with capture_sql(sessions) as statements:
        initial = await writer.put(key, b"initial", if_none_match=True)
        with pytest.raises(ObjectConflict):
            await writer.put(key, b"conflicting", if_none_match=True)
        assert (await writer.stat(key)).version == initial.version
        with pytest.raises(ObjectStoreUnavailable, match="conditional write"):
            await writer.put(key, b"unconditional")
        replaced = await writer.put(key, b"checkpoint", if_match=initial.version)
        with pytest.raises(ObjectConflict):
            await writer.put(key, b"stale", if_match=initial.version)
    assert statements == []
    assert replaced.version != initial.version
    async with writer.open(key) as reader:
        assert b"".join([part async for part in reader]) == b"checkpoint"


async def test_unregistered_run_state_waits_for_orphan_minimum_age(collection_sessions, object_store):
    sessions = collection_sessions
    key = f"organizations/{ORGANIZATION_ID}/runs/run_orphan/state.json"
    info = await PublicationObjectStore(object_store, sessions).put(key, b"initial", if_none_match=True)
    clock = _Clock(info.modified_at + timedelta(hours=24))
    collector = _collector(sessions, object_store, clock)
    with capture_sql(sessions) as statements:
        assert await collector.collect_unowned(key) is False
    assert statements == []
    assert (await object_store.stat(key)).version == info.version
    clock.now += timedelta(microseconds=1)
    assert await collector.collect_unowned(key) is True
    with pytest.raises(ObjectNotFound):
        await object_store.stat(key)


@pytest.mark.parametrize("filename", ["state.json", "display_messages.json"])
async def test_collect_only_unowned_namespaces(collection_sessions, object_store, filename):
    sessions = collection_sessions
    await seed_run_and_secret(sessions)
    clock = _Clock(utc_now())
    writer = PublicationObjectStore(object_store, sessions, clock=clock)
    retained = f"organizations/{ORGANIZATION_ID}/runs/{RUN_ID}/{filename}"
    orphan = f"organizations/{ORGANIZATION_ID}/runs/run_orphan/{filename}"
    unknown = f"organizations/{ORGANIZATION_ID}/unknown/data"
    for key in (retained, orphan, unknown):
        await writer.put(key, b"body", if_none_match=True)
    clock.now += timedelta(days=2)
    collector = _collector(sessions, object_store, clock)
    assert (await collector.scan()).completed == 1
    assert (await collector.scan()).completed == 0
    with pytest.raises(ObjectNotFound):
        await object_store.stat(orphan)
    for key in (retained, unknown):
        assert (await object_store.stat(key)).size == 4


@pytest.mark.parametrize("object_store", ["local"], indirect=True)
async def test_expired_collector_cannot_delete_republished_identical_bytes(
    collection_sessions, object_store, monkeypatch
):
    sessions = collection_sessions
    clock = _Clock(utc_now())
    writer = PublicationObjectStore(object_store, sessions, clock=clock)
    digest = "d" * 64
    key = skill_package_object_key(ORGANIZATION_ID, WORKSPACE_ID, digest)
    original = await writer.put(key, b"immutable bytes", if_none_match=True)
    clock.now += timedelta(days=2)
    entered, release = anyio.Event(), anyio.Event()
    raw_delete = object_store.delete

    async def delayed_delete(key, *, if_match=None):
        entered.set()
        await release.wait()
        await raw_delete(key, if_match=if_match)

    monkeypatch.setattr(object_store, "delete", delayed_delete)
    results = []

    async def collect():
        results.append(await _collector(sessions, object_store, clock).scan())

    async with anyio.create_task_group() as group:
        group.start_soon(collect)
        await entered.wait()
        # The deletion transaction has closed; another transaction can inspect
        # its fence immediately and cannot commit a new reference to those bytes.
        with anyio.fail_after(2):
            with pytest.raises(ApplicationError, match="publication changed"):
                async with transaction(sessions) as database:
                    await require_object_publications(database, (key,))
        clock.now += timedelta(seconds=31)
        with pytest.raises(ObjectConflict):
            await writer.put(key, b"different losing body", if_none_match=True)
        current = await object_store.stat(key)
        assert current.version != original.version
        async with transaction(sessions) as database:
            await require_object_publications(database, (key,))
            database.add(_upload(digest, clock.now))
        release.set()
    assert results[0].completed == 0
    async with object_store.open(key) as reader:
        assert b"".join([part async for part in reader]) == b"immutable bytes"
    async with short_session(sessions) as database:
        assert (await database.get(ObjectPublicationRecord, key)).phase == "ready"


async def test_lost_delete_acknowledgement_recovers_without_object_listing(
    collection_sessions, object_store, monkeypatch
):
    sessions = collection_sessions
    clock = _Clock(utc_now())
    key = skill_package_object_key(ORGANIZATION_ID, WORKSPACE_ID, "e" * 64)
    await PublicationObjectStore(object_store, sessions, clock=clock).put(key, b"body", if_none_match=True)
    clock.now += timedelta(days=2)
    raw_delete = object_store.delete

    async def lost_ack(key, *, if_match=None):
        await raw_delete(key, if_match=if_match)
        raise ObjectStoreUnavailable("delete acknowledgement lost")

    monkeypatch.setattr(object_store, "delete", lost_ack)
    assert (await _collector(sessions, object_store, clock).scan()).failed == 1
    monkeypatch.setattr(object_store, "delete", raw_delete)
    clock.now += timedelta(seconds=31)
    assert (await _collector(sessions, object_store, clock).recover()).completed == 1
    async with short_session(sessions) as database:
        assert (await database.get(ObjectPublicationRecord, key)).phase == "collected"


async def test_upload_ownership_and_recent_publication_block_collection(collection_sessions, object_store):
    sessions = collection_sessions
    clock = _Clock(utc_now())
    digest = "f" * 64
    key = skill_package_object_key(ORGANIZATION_ID, WORKSPACE_ID, digest)
    writer = PublicationObjectStore(object_store, sessions, clock=clock)
    await writer.put(key, b"package", if_none_match=True)
    assert (await _collector(sessions, object_store, clock).scan()).completed == 0
    clock.now += timedelta(days=2)
    async with transaction(sessions) as database:
        await require_object_publications(database, (key,))
        database.add(_upload(digest, clock.now))
    assert (await _collector(sessions, object_store, clock).scan()).completed == 0
    clock.now += timedelta(days=2)
    assert (await _collector(sessions, object_store, clock).scan()).completed == 1


async def test_expired_publisher_cannot_finish_or_select_missing_bytes(collection_sessions, object_store):
    sessions = collection_sessions
    clock = _Clock(utc_now())
    writer = PublicationObjectStore(object_store, sessions, timeout_seconds=10, clock=clock)
    key = skill_package_object_key(ORGANIZATION_ID, WORKSPACE_ID, "1" * 64)
    generation = await writer._begin(key)
    info = await object_store.put(key, b"staged", if_none_match=True)
    clock.now += timedelta(days=2)
    assert (await _collector(sessions, object_store, clock).recover()).completed == 1
    with pytest.raises(ObjectStoreUnavailable, match="expired"):
        await writer._finish(key, generation, info.version)
    with pytest.raises(ApplicationError):
        async with transaction(sessions) as database:
            await require_object_publications(database, (key,))
    with pytest.raises(ObjectNotFound):
        await object_store.stat(key)


async def test_profile_image_collection_retains_only_current_owner_references(collection_sessions, object_store):
    from a13n_service.iam.models import UserRecord, WorkspaceRecord

    sessions = collection_sessions
    await seed_run_and_secret(sessions)
    clock = _Clock(utc_now())
    writer = PublicationObjectStore(object_store, sessions, clock=clock)
    current = f"users/{USER_ID}/profile/avatar/img_current/content.webp"
    previous = f"users/{USER_ID}/profile/avatar/img_previous/content.webp"
    icon = f"organizations/{ORGANIZATION_ID}/workspaces/{WORKSPACE_ID}/profile/icon/img_current/content.webp"
    abandoned = f"organizations/{ORGANIZATION_ID}/profile/icon/img_abandoned/content.webp"
    for key in (current, previous, icon, abandoned):
        await writer.put(key, b"image", if_none_match=True)
    async with transaction(sessions) as session:
        user = UserRecord(
            id=USER_ID,
            email="profile@example.com",
            normalized_email="profile@example.com",
            name="Profile Owner",
            status="active",
            email_verified_at=clock.now,
            created_at=clock.now,
            updated_at=clock.now,
        )
        session.add(user)
        workspace = await session.get(WorkspaceRecord, WORKSPACE_ID)
        user.image_id = workspace.image_id = "img_current"
    clock.now += timedelta(days=2)
    collector = _collector(sessions, object_store, clock)
    assert (await collector.scan()).completed == 1
    assert (await collector.scan()).completed == 1
    for key in (current, icon):
        assert (await object_store.stat(key)).size == 5
    for key in (previous, abandoned):
        with pytest.raises(ObjectNotFound):
            await object_store.stat(key)
