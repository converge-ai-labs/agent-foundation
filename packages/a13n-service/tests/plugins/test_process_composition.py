import hashlib
from pathlib import Path

import pytest
from a13n_service.app import Components, create_app
from a13n_service.plugins import BuiltinPluginArtifact, BuiltinPluginRegistration
from a13n_service.plugins.models import PluginRecord, PluginVersionRecord
from a13n_service.settings import ProcessRole, Settings
from a13n_service.storage import short_session

from .conftest import build_wheel, wheel_body


@pytest.mark.anyio
async def test_control_lifespan_registers_distribution_builtin_plugins(
    tmp_path: Path,
    service_sqlite_database: Path,
) -> None:
    wheel = build_wheel()
    registration = BuiltinPluginRegistration(
        plugin_id="plg_builtinaudit0001",
        plugin_version_id="plgv_builtinauditv100",
        system_actor_id="sa_pluginrelease0001",
        plugin_key="acme.audit",
        distribution_name="acme-audit",
        top_level_package="acme_audit",
        version="1.0.0",
        content_digest=hashlib.sha256(wheel).hexdigest(),
        required=True,
    )
    app = create_app(
        Settings(
            _env_file=None,
            iam_initial_admin_email="admin@example.com",
            role=ProcessRole.control,
            database_backend="sqlite",
            database_sqlite_path=service_sqlite_database,
            redis_backend="memory",
            object_backend="local",
            object_local_root=tmp_path / "objects",
            filesystem_root=tmp_path / "files",
            secret_master_key_base64="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
            secret_encryption_key_id="a13n-service-test-key",
            connectivity_public_origin="http://testserver",
            connectivity_http_origins=("http://testserver",),
        ),
        components=Components(
            builtin_plugin_artifacts=(
                BuiltinPluginArtifact(
                    registration=registration,
                    body_factory=lambda: wheel_body(wheel),
                    content_length=len(wheel),
                ),
            ),
        ),
    )

    async with app.router.lifespan_context(app):
        async with short_session(app.state.runtime.shared.storage.sessions) as session:
            plugin = await session.get(PluginRecord, registration.plugin_id)
            version = await session.get(PluginVersionRecord, registration.plugin_version_id)

        assert plugin is not None and plugin.required is True
        assert plugin.source == "builtin"
        assert version is not None and version.content_digest == registration.content_digest
