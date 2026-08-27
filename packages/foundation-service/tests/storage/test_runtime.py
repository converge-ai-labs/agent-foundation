from pathlib import Path

import pytest
from a13n_service.storage import StorageSettings, open_storage, short_session
from a13n_service.storage.runtime import StorageStartupError
from sqlalchemy import text

pytestmark = pytest.mark.anyio


def _settings(tmp_path: Path) -> StorageSettings:
    return StorageSettings.model_validate(
        {
            "database": {"backend": "sqlite", "path": tmp_path / "database.sqlite3"},
            "redis": {"backend": "memory"},
            "objects": {"backend": "local", "root": tmp_path / "objects"},
            "filesystem": {"root": tmp_path / "files", "worker_limit": 3},
        }
    )


async def test_local_runtime_constructs_one_backend_per_capability(tmp_path: Path) -> None:
    async with open_storage(_settings(tmp_path)) as storage:
        async with short_session(storage.sessions) as session:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        assert await storage.redis.set(b"ready", b"yes")
        await storage.objects.put("probe", b"ready")
        assert (await storage.objects.stat("probe")).size == 5
        assert storage.files_root == (tmp_path / "files").resolve()
        assert storage.file_limiter.total_tokens == 3


async def test_runtime_failure_is_typed_and_does_not_fallback(tmp_path: Path) -> None:
    value = _settings(tmp_path).model_dump()
    value["filesystem"] = {"root": tmp_path / "missing", "create_root": False}
    settings = StorageSettings.model_validate(value)

    with pytest.raises(StorageStartupError) as captured:
        async with open_storage(settings):
            pytest.fail("runtime unexpectedly started")

    assert isinstance(captured.value.__cause__, FileNotFoundError)
    assert not (tmp_path / "objects").exists()


async def test_runtime_preserves_errors_from_the_consumer(tmp_path: Path) -> None:
    class ConsumerError(RuntimeError):
        pass

    with pytest.raises(ConsumerError, match="consumer failed"):
        async with open_storage(_settings(tmp_path)):
            raise ConsumerError("consumer failed")


async def test_network_runtime_failure_does_not_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    s3_service,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", s3_service.access_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", s3_service.secret_key)
    settings = StorageSettings.model_validate(
        {
            "database": {"backend": "sqlite", "path": tmp_path / "database.sqlite3"},
            "redis": {"backend": "memory"},
            "objects": {
                "backend": "s3",
                "bucket": "bucket-does-not-exist",
                "endpoint_url": s3_service.endpoint_url,
                "force_path_style": True,
            },
            "filesystem": {"root": tmp_path / "files"},
        }
    )

    with pytest.raises(StorageStartupError, match="initialization failed"):
        async with open_storage(settings):
            pytest.fail("runtime unexpectedly started")

    assert not (tmp_path / "objects").exists()
