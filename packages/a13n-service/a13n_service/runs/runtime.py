"""The process-wide dependencies run operations use, assembled once by `app.py`."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property, partial
from typing import TYPE_CHECKING

from a13n_harness import HarnessInstrumentation
from a13n_harness.environment import EnvironmentMount
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.modal.pool import TransportPool
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, after_commit
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.infra.redis import wake
from a13n_service.infra.tasks import Tasks
from a13n_service.providers.metrics import Queries
from a13n_service.providers.registry import Registry
from a13n_service.providers.traces import TraceProvider
from a13n_service.resources.builtin_skills import BuiltinPackage, BuiltinSkillName
from a13n_service.runs.admission import AdmissionPolicy
from a13n_service.runs.mailbox import Mailbox
from a13n_service.settings import Settings
from a13n_service.tenancy.access import Access
from a13n_service.tenancy.workspaces import WorkspaceCreated

if TYPE_CHECKING:
    from a13n_service.runs.compaction_model import BackgroundCompaction
    from a13n_service.runs.environments.monitoring.proxy import Proxy
    from a13n_service.runs.tool_executions.owner import Executions


@dataclass(frozen=True)
class Runtime:
    storage: Storage
    objects: ObjectStore
    redis: Redis
    keys: KeyRing
    settings: Settings
    registry: Registry
    platform_mounts: dict[str, EnvironmentMount]
    builtin_skills: dict[BuiltinSkillName, BuiltinPackage]
    # Who may call and what their grants mean: the distribution's authenticator, roles and grant sources.
    access: Access
    tasks: Tasks
    modal_transports: TransportPool | None = field(default=None, repr=False)
    metrics_queries: Queries = field(default_factory=Queries)
    metrics_proxy: Proxy | None = None
    workspace_created: WorkspaceCreated | None = None
    admission: AdmissionPolicy | None = None
    # Records Harness spans and metrics of worker attempts; None when tracing and metrics are both off.
    instrumentation: HarnessInstrumentation | None = None
    # The Harness plugin factories the deployment installed; agent configurations select instances of them.
    plugins: HarnessPluginFactoryCatalog = field(default_factory=lambda: HarnessPluginFactoryCatalog(()))
    # The backend trace queries read; None when tracing is off.
    traces: TraceProvider | None = None

    @cached_property
    def mailbox(self) -> Mailbox:
        return Mailbox(self.storage, interval=self.settings.worker.authority_seconds)

    @cached_property
    def executions(self) -> Executions:
        from a13n_service.runs.tool_executions.owner import Executions

        return Executions(self)

    @cached_property
    def compactions(self) -> BackgroundCompaction:
        from a13n_service.runs.compaction_model import BackgroundCompaction

        return BackgroundCompaction(self)

    @property
    def endpoint_policy(self) -> EndpointPolicy:
        """Which provider and connection endpoints outbound requests may reach."""
        return self.settings.providers.endpoint_policy

    def wake_workers(self, session: AsyncSession) -> None:
        """After commit, tell idle workers to scan now; the periodic scan covers a lost wakeup."""
        after_commit(session, partial(wake, self.redis, timeout=self.settings.redis.timeout))
