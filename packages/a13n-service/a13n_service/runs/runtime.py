"""The process-wide dependencies run operations use, assembled once by `app.py`."""

from dataclasses import dataclass, field
from functools import partial

from a13n_harness import HarnessInstrumentation
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, after_commit
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.infra.redis import wake
from a13n_service.infra.tasks import Tasks
from a13n_service.providers.registry import Registry
from a13n_service.providers.traces import TraceProvider
from a13n_service.runs.admission import AdmissionPolicy
from a13n_service.settings import Settings
from a13n_service.tenancy.access import Access
from a13n_service.tenancy.workspaces import WorkspaceCreated


@dataclass(frozen=True)
class Runtime:
    storage: Storage
    objects: ObjectStore
    redis: Redis
    keys: KeyRing
    settings: Settings
    registry: Registry
    # Who may call and what their grants mean: the distribution's authenticator, roles and grant sources.
    access: Access
    tasks: Tasks
    workspace_created: WorkspaceCreated | None = None
    admission: AdmissionPolicy | None = None
    # Records Harness spans and metrics of worker attempts; None when tracing and metrics are both off.
    instrumentation: HarnessInstrumentation | None = None
    # The Harness plugin factories the deployment installed; agent configurations select instances of them.
    plugins: HarnessPluginFactoryCatalog = field(default_factory=lambda: HarnessPluginFactoryCatalog(()))
    # The backend trace queries read; None when tracing is off.
    traces: TraceProvider | None = None

    @property
    def endpoint_policy(self) -> EndpointPolicy:
        """Which provider and connection endpoints outbound requests may reach."""
        return self.settings.providers.endpoint_policy

    def wake_workers(self, session: AsyncSession) -> None:
        """After commit, tell idle workers to scan now; the periodic scan covers a lost wakeup."""
        after_commit(session, partial(wake, self.redis, timeout=self.settings.redis.timeout))
