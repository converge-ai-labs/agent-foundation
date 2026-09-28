"""Initialize once after workspace commit, and catch up existing workspaces once at process startup.

External preparation holds no database session. A workspace lock serializes the atomic resource pair and
completion fact with other initializers and archiving. Failed items get bounded process-owned retries; there
is no periodic reconciliation of resources users own after initialization.
"""

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_logging import exception_details, get_logger
from sqlalchemy import select

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, lock, short_session, transaction
from a13n_service.infra.tasks import Tasks
from a13n_service.providers.registry import Registry
from a13n_service.provisioning import docker, local
from a13n_service.provisioning.defaults import Defaults
from a13n_service.provisioning.tables import WorkspaceProvisioningRow
from a13n_service.resources.environment_templates.schemas import TemplateCreate
from a13n_service.resources.environment_templates.service import insert_template, validate_config
from a13n_service.resources.providers.service import insert_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.settings import Provisioning
from a13n_service.tenancy.authorize import WorkspaceScope
from a13n_service.tenancy.tables import WorkspaceRow

logger = get_logger(__name__)
RETRY_DELAYS = (1, 5)


class Initializer:
    def __init__(
        self,
        storage: Storage,
        registry: Registry,
        keys: KeyRing,
        tasks: Tasks,
        config: Provisioning,
        policy: EndpointPolicy,
    ) -> None:
        self.storage, self.registry, self.keys, self.tasks = storage, registry, keys, tasks
        self.components: dict[str, Callable[[], Awaitable[Defaults]]] = {}
        if config.local.enabled:
            self.components["local"] = partial(local.prepare, config.local)
        if config.docker.enabled:
            self.components["docker"] = partial(docker.prepare, config.docker, registry, policy)

    async def __call__(self, workspace_id: str) -> None:
        """Await each component's first attempt, without failing the already committed workspace creation."""
        await asyncio.gather(*(self._start(workspace_id, component) for component in self.components))

    async def existing(self) -> None:
        """One bounded-page traversal; completed components do no external work."""
        if not self.components:
            return
        after = ""
        while True:
            async with short_session(self.storage) as session:
                ids = list(
                    await session.scalars(
                        select(WorkspaceRow.id)
                        .where(WorkspaceRow.archived_at.is_(None), WorkspaceRow.id > after)
                        .order_by(WorkspaceRow.id)
                        .limit(100)
                    )
                )
            if not ids:
                return
            for workspace_id in ids:
                await self(workspace_id)
            after = ids[-1]

    async def _start(self, workspace_id: str, component: str) -> None:
        if not await self._attempt(workspace_id, component):
            self.tasks.start(self._retry(workspace_id, component), name=f"provision-{component}-{workspace_id}")

    async def _retry(self, workspace_id: str, component: str) -> None:
        for delay in RETRY_DELAYS:
            await asyncio.sleep(delay)
            if await self._attempt(workspace_id, component):
                return

    async def _attempt(self, workspace_id: str, component: str) -> bool:
        try:
            await self._ensure(workspace_id, component)
        except Exception as error:
            logger.warning(
                "Workspace provisioning failed; startup can retry unfinished components",
                extra={
                    "workspace_id": workspace_id,
                    "component": component,
                    "exception_details": exception_details(error),
                },
            )
            return False
        return True

    async def _ensure(self, workspace_id: str, component: str) -> None:
        key = (workspace_id, component)
        async with short_session(self.storage) as session:
            if await session.get(WorkspaceProvisioningRow, key) is not None:
                return
            workspace = await session.get(WorkspaceRow, workspace_id)
            if workspace is None or workspace.archived_at is not None:
                return
        defaults = await self.components[component]()
        config = validate_config(self.registry, defaults.provider.type, defaults.template_config)
        async with transaction(self.storage) as session:
            workspace = await lock(session, WorkspaceRow, workspace_id)
            if workspace is None or workspace.archived_at is not None:
                return
            if await session.get(WorkspaceProvisioningRow, key) is not None:
                return
            scope = WorkspaceScope(workspace.organization_id, workspace.id)
            provider = await insert_provider(
                session,
                None,
                EnvironmentProviderRow,
                scope,
                defaults.provider,
                registry=self.registry,
                keys=self.keys,
            )
            template = await insert_template(
                session,
                None,
                scope,
                TemplateCreate(name=defaults.template_name, provider_id=provider.id, config=defaults.template_config),
                config=config,
            )
            session.add(
                WorkspaceProvisioningRow(
                    organization_id=scope.organization_id,
                    workspace_id=workspace_id,
                    component=component,
                    provider_id=provider.id,
                    template_id=template.id,
                )
            )
