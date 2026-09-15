"""External mode never falls back to Docker or mutates caller-owned databases."""

from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from ..infrastructure import dependencies


def external():
    return dependencies.ExternalDependencies(
        postgres_admin_url="postgresql://test:private-db-password@127.0.0.1:5432/existing",
        redis_url="redis://127.0.0.1:6379/0",
        object_endpoint_url="http://127.0.0.1:9000",
        aws_access_key_id="private-access",
        aws_secret_access_key="private-secret",
    )


def test_default_mode_does_not_read_external_configuration(monkeypatch):
    monkeypatch.delenv(dependencies.MODE_ENV, raising=False)
    monkeypatch.setenv(dependencies.CONFIG_ENV, "/must-not-be-read")
    assert dependencies.load_external_dependencies() is None


@pytest.mark.parametrize("contents", [None, "broken TOML", 'postgres_admin_url = "private-db-password"'])
def test_external_mode_fails_closed_without_disclosing_configuration(monkeypatch, tmp_path, contents):
    monkeypatch.setenv(dependencies.MODE_ENV, "external")
    monkeypatch.delenv(dependencies.CONFIG_ENV, raising=False)
    if contents is not None:
        path = tmp_path / "infrastructure.toml"
        path.write_text(contents)
        monkeypatch.setenv(dependencies.CONFIG_ENV, str(path))
    with pytest.raises(ValueError) as caught:
        dependencies.load_external_dependencies()
    assert "private-db-password" not in str(caught.value)


@pytest.mark.parametrize("mode,path", [("external", None), ("docker", "unexpected.toml")])
def test_infrastructure_modes_require_unambiguous_explicit_options(mode, path):
    with pytest.raises(ValueError):
        dependencies.infrastructure_environment(SimpleNamespace(infrastructure=mode, infrastructure_config=path))


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [None, "workload", "lost-create-reply"])
async def test_external_database_cleanup_is_limited_to_each_allocated_database(monkeypatch, failure, capsys):
    statements = []
    connects = []

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            statements.append(statement.as_string())
            if failure == "lost-create-reply" and statements[-1].startswith("CREATE"):
                raise RuntimeError("lost create reply")

    async def connect(url, **options):
        connects.append(url)
        assert options["autocommit"] and options["connect_timeout"] == 5
        return Connection()

    monkeypatch.setattr(dependencies.psycopg.AsyncConnection, "connect", connect)

    async def run():
        async with dependencies.external_database(external()) as url:
            parsed = make_url(url)
            assert parsed.database.startswith("a13n_live_") and parsed.database != "existing"
            assert parsed.drivername == "postgresql+psycopg" and parsed.password == "private-db-password"
            if failure == "workload":
                raise RuntimeError("workload failed")

    if failure:
        with pytest.raises(RuntimeError):
            await run()
    else:
        await run()
        await run()
    names = []
    for create, drop in zip(statements[::2], statements[1::2], strict=True):
        name = create.removeprefix("CREATE DATABASE ")
        names.append(name)
        assert name.startswith('"a13n_live_')
        assert drop == f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"
    assert len(names) == len(set(names))
    assert all(url.endswith("/existing") for url in connects)
    assert "private-db-password" not in capsys.readouterr().out


@pytest.mark.anyio
async def test_external_storage_uses_explicit_credentials_and_never_starts_docker(monkeypatch):
    from contextlib import asynccontextmanager

    from ..infrastructure import local_storage

    buckets = []
    removed = []

    class S3:
        async def create_bucket(self, *, Bucket):
            buckets.append(Bucket)

        async def list_buckets(self):
            return {}

        async def list_objects_v2(self, *, Bucket, **options):
            assert Bucket in buckets
            return {}

        async def delete_bucket(self, *, Bucket):
            removed.append(Bucket)

    @asynccontextmanager
    async def create_client(*args, **options):
        assert options["aws_access_key_id"] == "private-access"
        assert options["aws_secret_access_key"] == "private-secret"
        assert options["aws_session_token"] == ""
        yield S3()

    async def forbidden(*args):
        pytest.fail("External mode started a Docker container")

    async def compatible(self):
        pass

    monkeypatch.setattr(local_storage, "start_rustfs", forbidden)
    monkeypatch.setattr(local_storage, "get_session", lambda: SimpleNamespace(create_client=create_client))
    monkeypatch.setattr(local_storage.S3ObjectStore, "check_compatibility", compatible)
    for _ in range(2):
        async with local_storage.open_object_storage(**external().object_options()) as environment:
            assert environment["AWS_ACCESS_KEY_ID"] == "private-access"
    assert len(set(buckets)) == 2 and removed == buckets
