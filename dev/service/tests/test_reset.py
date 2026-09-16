"""Exercise complete reset and browser-authenticated seed data in owned containers."""

import hashlib
import json
import random
import socket
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx2
import pytest
from a13n_service.app import create_app
from a13n_service.configuration.sources import load_settings
from a13n_service.storage import open_storage, short_session
from sqlalchemy import text

from dev.service.environment import LOCAL_CONFIG
from dev.service.lifecycle import lifecycle_lock
from dev.service.reset import reset
from dev.service.seed import PASSWORD, seed
from dev.service.tests.support import environment_for


def free_port():
    # Stay below the host's ephemeral client-port range while Compose starts.
    for _ in range(100):
        with socket.socket() as listener:
            port = random.randrange(20000, 30000)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free local test port")


def locked_reset(environment, state):
    with lifecycle_lock(environment.root):
        reset(environment, state)


@pytest.fixture
def environment(tmp_path):
    root = tmp_path.resolve()
    ports = set()
    while len(ports) < 3:
        ports.add(free_port())
    postgres_port, redis_port, model_port = sorted(ports)
    settings = load_settings(
        LOCAL_CONFIG,
        environ={},
        overrides={
            # Disposable reset tests never export to or query the developer's Langfuse.
            "observability": {"tracing": False, "query": {"provider": "none"}},
            "worker": {"concurrency": 2},
            "database": {
                "url": f"postgresql+psycopg://a13n_service_dev:local-only-password@127.0.0.1:{postgres_port}/a13n_service_dev"
            },
            "redis": {"url": f"redis://127.0.0.1:{redis_port}/0"},
            "connectivity": {"http_origins": ["http://127.0.0.1:5173", f"http://127.0.0.1:{model_port}"]},
            "objects": {"local_root": root / "var/service/objects"},
            "filesystem": {"root": root / "var/service/files"},
        },
    )
    value = environment_for(settings, root)
    yield value
    value.compose("down", "--volumes", "--remove-orphans")


def test_reset_seeded_then_empty_clears_all_stores_and_invalidates_login(environment, monkeypatch):
    async def small_seed(settings, *, model_port):
        assert model_port == urlsplit(settings.connectivity.http_origins[-1]).port
        return await seed(settings, session_count=3, model_port=model_port)

    monkeypatch.setattr("dev.service.seed.seed", small_seed)
    locked_reset(environment, "seeded")
    manifest = json.loads((environment.state / "seed.json").read_text())
    assert manifest["bulk_session_count"] == 3
    bulk_environments = manifest["bulk_environments"]
    assert len(bulk_environments) == 2
    assert len({value["environment_id"] for value in bulk_environments}) == 2
    roots = [Path(value["root"]).resolve() for value in bulk_environments]
    assert len(set(roots)) == 2
    assert all(root.is_relative_to(environment.settings.filesystem.root / "bulk") for root in roots)
    assert all(root.is_dir() for root in roots)
    assert manifest["session_count"] >= 15
    assert len(manifest["asset_ids"]) == 65
    assert len(manifest["skill_ids"]) == 64
    assert len(manifest["agent_ids"]) == 56
    assert set(manifest["coverage"]["run_statuses"]) == {"completed", "failed", "waiting", "cancelled"}
    assert manifest["coverage"]["long_conversation_runs"] == 13
    assert (environment.state / "seed-report.md").exists()
    assert not environment.incomplete.exists()

    async def verify_seed():
        app = create_app(environment.settings)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app),
                base_url="https://127.0.0.1:5173",
                headers={"Origin": "http://127.0.0.1:5173"},
                trust_env=False,
            ) as client:
                login = await client.post(
                    "/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
                )
                assert login.status_code == 200
                cookies = dict(client.cookies)
                client.headers["X-A13N-Workspace-ID"] = manifest["workspace_id"]
                for asset in manifest["asset_checks"]:
                    content = await client.get(f"/api/v1/assets/{asset['id']}/content")
                    assert content.status_code == 200
                    assert len(content.content) == asset["size"]
                    assert hashlib.sha256(content.content).hexdigest() == asset["sha256"]
                for workspace in bulk_environments:
                    instance = await client.get(f"/api/v1/environments/{workspace['environment_id']}")
                    assert instance.status_code == 200
                    revision = await client.get(
                        f"/api/v1/environment-template-revisions/{instance.json()['template_revision_id']}"
                    )
                    assert revision.status_code == 200
                    assert revision.json()["configuration"]["root"]["path"] == workspace["root"]
                async with short_session(app.state.runtime.shared.storage.sessions) as session:
                    for workspace in bulk_environments:
                        runs = (
                            await session.execute(
                                text(
                                    "SELECT created_at, sealed_at FROM runs WHERE environment_id = :environment ORDER BY created_at"
                                ),
                                {"environment": workspace["environment_id"]},
                            )
                        ).all()
                        assert runs
                        assert all(current.created_at >= previous.sealed_at for previous, current in pairwise(runs))
                sessions = await client.get(f"/api/v1/workspaces/{manifest['workspace_id']}/sessions")
                assert sessions.status_code == 200
                assert len(sessions.json()["items"]) == manifest["session_count"]
                waiting_id = manifest["scenarios"]["conversations"]["waiting_for_client"]
                pending = await client.get(f"/api/v1/runs/{waiting_id}/pending-actions")
                assert pending.status_code == 200 and pending.json()["items"][0]["kind"] == "client_tool"
                client.headers["X-A13N-Workspace-ID"] = manifest["empty_workspace_id"]
                empty = await client.get(f"/api/v1/workspaces/{manifest['empty_workspace_id']}/agents")
                assert empty.status_code == 200 and empty.json()["items"] == []
                await app.state.runtime.shared.storage.redis.set("local-reset-evidence", "must-disappear")
                return cookies

    old_cookies = anyio.run(verify_seed)
    (environment.settings.filesystem.root / "user-created.txt").write_text("must disappear")
    locked_reset(environment, "empty")
    assert not (environment.state / "seed.json").exists()
    assert not (environment.settings.filesystem.root / "user-created.txt").exists()
    assert not environment.settings.objects.local_root.exists()

    async def verify_empty():
        async with open_storage(environment.settings.storage_settings()) as store:
            async with short_session(store.sessions) as session:
                assert (await session.execute(text("SELECT count(*) FROM users"))).scalar_one() == 0
                assert (await session.execute(text("SELECT count(*) FROM assets"))).scalar_one() == 0
            assert await store.redis.get("local-reset-evidence") is None
        app = create_app(environment.settings)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app), base_url="https://127.0.0.1:5173", cookies=old_cookies
            ) as client:
                assert (await client.get("/api/v1/users/me")).status_code == 401

    anyio.run(verify_empty)
    locked_reset(environment, "empty")


def test_reset_rejects_active_database_connection(environment):
    locked_reset(environment, "empty")

    async def check():
        async with open_storage(environment.settings.storage_settings()) as store:
            async with short_session(store.sessions) as session:
                await session.execute(text("SELECT 1"))
                with pytest.raises(ValueError, match="active connections"):
                    await anyio.to_thread.run_sync(locked_reset, environment, "empty")

    anyio.run(check)


def test_seed_failure_leaves_incomplete_marker(environment, monkeypatch):
    async def broken(settings, *, model_port):
        raise RuntimeError("intentional seed failure")

    monkeypatch.setattr("dev.service.seed.seed", broken)
    with pytest.raises(RuntimeError):
        locked_reset(environment, "seeded")
    assert environment.incomplete.exists()
    locked_reset(environment, "empty")
    assert not environment.incomplete.exists()
