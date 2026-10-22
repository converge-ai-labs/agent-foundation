"""Worker-local read-only mounts for platform documentation, schemas and the deployment catalog."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness.capabilities.skills import FileSkillSource
from a13n_harness.environment import EnvironmentMount
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import (
    EnvironmentAction,
    EnvironmentDescriptor,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL

from a13n_service.providers.registry import Registry, web_operations
from a13n_service.resources.agents.toolsets import catalog
from a13n_service.resources.providers.service import list_provider_types
from a13n_service.settings import Settings

ROOT = "/platform-skills/platform-operations"
INSTANCE_ROOT = "/platform-instance"
SOURCE = FileSkillSource("a13n-service-builtin-skills", (ROOT,))
_PACKAGE = Path(__file__).parent.parent / "builtin_skills"
_READ_TEXT = EnvironmentPermissionSet(
    operations=frozenset(
        {
            EnvironmentAction.FILE_STAT,
            EnvironmentAction.FILE_READ_TEXT,
            EnvironmentAction.FILE_LIST,
            EnvironmentAction.FILE_QUERY,
            EnvironmentAction.FILE_SEARCH_TEXT,
        }
    )
)


@dataclass(frozen=True, slots=True)
class _LocalSource:
    connector: EnvironmentConnector

    @property
    def provider_key(self) -> str:
        return self.connector.provider_key

    @property
    def environment_id(self) -> str:
        return self.connector.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.connector.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.connector.state

    async def ensure_ready(self) -> EnvironmentConnector:
        return self.connector


def _mount(root: Path, path: str) -> EnvironmentMount:
    connector = DIRECT_LOCAL.execution_connector({"root": {"path": root}, "max_value_bytes": 4 * 1024 * 1024})
    return EnvironmentMount(_LocalSource(connector), permission_ceiling=_READ_TEXT, mount_path=path)


@asynccontextmanager
async def open_platform_mounts(registry: Registry, settings: Settings) -> AsyncGenerator[dict[str, EnvironmentMount]]:
    """Prepare deployment-specific references once for the process, outside individual attempts."""
    instance = {
        "providers": {
            kind: list_provider_types(registry, kind).model_dump(mode="json")
            for kind in ("model", "environment", "connector", "web")
        },
        "toolsets": catalog(
            frozenset(operation for definition in registry.web.values() for operation in web_operations(definition))
        ).model_dump(mode="json"),
        "plugins": list(settings.plugins.keys),
    }
    directory = await asyncio.to_thread(TemporaryDirectory, prefix="a13n-platform-")
    try:
        root = Path(directory.name)
        await asyncio.to_thread((root / "catalog.json").write_text, json.dumps(instance, indent=2), encoding="utf-8")
        yield {"builtin-skills": _mount(_PACKAGE, "/platform-skills"), "builtin-instance": _mount(root, INSTANCE_ROOT)}
    finally:
        await asyncio.to_thread(directory.cleanup)
