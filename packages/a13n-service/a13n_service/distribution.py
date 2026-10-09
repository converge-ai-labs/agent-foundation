"""What a build contributes, listed explicitly: no package scanning, no import-time registration.

A distribution extends the core by `OSS.extend(Distribution(...))`; duplicate tables, routes, settings
sections and roles fail at assembly, so an extension can never silently shadow a core operation. Background
work, the outbox deliveries included, is wired here from settings.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import MappingProxyType

from a13n_harness.providers.connector.builtins import BUILT_IN_CONNECTOR_PROVIDERS
from a13n_harness.providers.memory import BUILT_IN_MEMORY_PROVIDERS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.web.builtins import built_in_web_providers
from fastapi import APIRouter
from sqlalchemy import MetaData, Table

from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import Base, schema_rules, table_rules
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import Delivery, OutboxRow, purge_settled
from a13n_service.infra.sweeps import Sweep
from a13n_service.providers.environments import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_service.providers.registry import RegisteredProvider
from a13n_service.provisioning.tables import WorkspaceProvisioningRow
from a13n_service.resources.agents.routes import router as agents_router
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.assets.routes import router as assets_router
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.resources.connections.operations import recover_operations
from a13n_service.resources.connections.routes import router as connections_router
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.connector_providers.routes import router as connector_providers_router
from a13n_service.resources.environment_templates.routes import router as environment_templates_router
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.memories.purge import MemoryPurger
from a13n_service.resources.memories.routes import router as memories_router
from a13n_service.resources.memories.service import PURGE as MEMORY_PURGE
from a13n_service.resources.memories.tables import (
    MemoryFileRevisionRow,
    MemoryFileRow,
    MemoryFileStoreRow,
    MemoryRow,
)
from a13n_service.resources.models.routes import router as models_router
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.providers.routes import router as providers_router
from a13n_service.resources.providers.tables import (
    ConnectorProviderRow,
    EnvironmentProviderRow,
    MemoryProviderRow,
    ModelProviderOAuthRow,
    ModelProviderRow,
    WebProviderRow,
)
from a13n_service.resources.skills.routes import router as skills_router
from a13n_service.resources.skills.tables import SkillRevisionRow, SkillRow
from a13n_service.resources.subscriptions.delivery import WebhookSender
from a13n_service.resources.subscriptions.routes import router as subscriptions_router
from a13n_service.resources.subscriptions.tables import SubscriptionRow
from a13n_service.resources.uploads.routes import router as uploads_router
from a13n_service.resources.uploads.tables import UploadRow
from a13n_service.runs.accept import ThreadAdvancer
from a13n_service.runs.admission import AdmissionPolicy
from a13n_service.runs.backlog import REPORT_SECONDS, BacklogReporter
from a13n_service.runs.checkpoints import CLEANUP, clean
from a13n_service.runs.children import child_results
from a13n_service.runs.environments.maintenance import maintenance_sweep, renewal_sweep
from a13n_service.runs.environments.routes import router as environments_router
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.memories.routes import router as thread_memories_router
from a13n_service.runs.memories.tables import ThreadMemoryRow
from a13n_service.runs.routes import router as runs_router
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.seal import LeaseExpirer
from a13n_service.runs.tables import (
    AttemptRow,
    InboxEntryRow,
    PendingAnswerRow,
    RunItemPageRow,
    RunRow,
    SessionRow,
    ThreadRow,
    UsageRecordRow,
)
from a13n_service.runs.trace_routes import router as traces_router
from a13n_service.settings import Section
from a13n_service.tenancy.access import Access, Authenticator, GrantSource
from a13n_service.tenancy.authenticate import LocalAuthenticator
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Verb
from a13n_service.tenancy.expiry import expire_credentials
from a13n_service.tenancy.mail import SmtpMailer, deliver_mail
from a13n_service.tenancy.member_routes import router as member_router
from a13n_service.tenancy.organization_routes import router as organization_router
from a13n_service.tenancy.routes import router as tenancy_router
from a13n_service.tenancy.tables import (
    ApiKeyRow,
    GrantRow,
    InvitationRow,
    OrganizationRow,
    PasswordRow,
    PrincipalRow,
    TokenRow,
    WorkspaceRow,
)
from a13n_service.usage.routes import router as usage_router

# Background work needs the assembled runtime, so a distribution lists factories.
type SweepFactory = Callable[[Runtime], Sweep]


@dataclass(frozen=True)
class Distribution:
    name: str
    routers: tuple[APIRouter, ...] = ()
    tables: tuple[type[Base], ...] = ()
    migrations: tuple[Path, ...] = ()
    settings: Mapping[str, type[Section]] = field(default_factory=dict)
    sweeps: tuple[SweepFactory, ...] = ()
    providers: tuple[RegisteredProvider, ...] = ()
    admission: AdmissionPolicy | None = None
    # None keeps the local password/session/API-key authenticator.
    authenticator: Authenticator | None = None
    grant_sources: tuple[GrantSource, ...] = ()
    # Role name to the verbs it grants; stored and sourced grants naming any other role fail closed.
    roles: Mapping[str, frozenset[Verb]] = field(default_factory=dict)

    def extend(self, extension: "Distribution") -> "Distribution":
        for name, mine, theirs in (
            ("settings section", self.settings, extension.settings),
            ("role", self.roles, extension.roles),
        ):
            if duplicate := set(mine) & set(theirs):
                raise ValueError(f"Duplicate {name}: {sorted(duplicate)}")
        if self.admission is not None and extension.admission is not None:
            raise ValueError("Only one admission policy may be installed")
        if self.authenticator is not None and extension.authenticator is not None:
            raise ValueError("Only one authenticator may be installed")
        return Distribution(
            name=extension.name,
            routers=self.routers + extension.routers,
            tables=self.tables + extension.tables,
            migrations=self.migrations + extension.migrations,
            settings=MappingProxyType({**self.settings, **extension.settings}),
            sweeps=self.sweeps + extension.sweeps,
            providers=self.providers + extension.providers,
            admission=extension.admission or self.admission,
            authenticator=extension.authenticator or self.authenticator,
            grant_sources=self.grant_sources + extension.grant_sources,
            roles=MappingProxyType({**self.roles, **extension.roles}),
        )

    def access(self) -> Access:
        """Who may call and what their grants mean; invalid role definitions fail here, at assembly."""
        return Access(self.authenticator or LocalAuthenticator(), self.roles, self.grant_sources)

    def metadata(self) -> MetaData:
        """The composed schema: table definitions and the rules each table declares in `info`."""
        metadata = MetaData(naming_convention=Base.metadata.naming_convention)
        for row in self.tables:
            table = row.__table__
            if not isinstance(table, Table):
                raise TypeError("Distribution rows must own concrete tables")
            if table.key in metadata.tables:
                raise ValueError(f"Duplicate table: {table.key}")
            table.to_metadata(metadata)
        return metadata

    def rules(self) -> list[str]:
        """Every rule in creation order, for schemas built from metadata (tests)."""
        return schema_rules(self.tables)

    def table_rules(self) -> dict[str, list[str]]:
        """Rules by table, for rendering into the migration that creates each table."""
        return {row.__tablename__: table_rules(row) for row in self.tables}


def _advance_threads(runtime: Runtime) -> Sweep:
    control = runtime.settings.control
    return Sweep(
        name="advance_threads",
        every=control.scan_seconds,
        run=ThreadAdvancer(runtime, batch=control.sweep_batch),
        timeout=max(30, control.scan_seconds * 10),
    )


def _expire_leases(runtime: Runtime) -> Sweep:
    worker = runtime.settings.worker
    return Sweep(
        name="expire_leases",
        every=worker.authority_seconds,
        run=LeaseExpirer(runtime, batch=runtime.settings.control.sweep_batch),
        timeout=max(30, worker.lease_seconds),
    )


def _expire_credentials(runtime: Runtime) -> Sweep:
    return Sweep(
        name="expire_credentials",
        every=runtime.settings.auth.expiry_scan_seconds,
        run=partial(expire_credentials, runtime.storage, limit=runtime.settings.control.sweep_batch),
        timeout=60,
    )


def _recover_connection_operations(runtime: Runtime) -> Sweep:
    return Sweep(
        name="recover_connection_operations",
        every=runtime.settings.providers.operation_scan_seconds,
        run=partial(recover_operations, runtime.storage, limit=runtime.settings.control.sweep_batch),
        timeout=60,
    )


def _deliver_outbox(runtime: Runtime) -> Sweep:
    settings = runtime.settings
    control = settings.control
    webhooks = WebhookSender(runtime.storage, runtime.keys, runtime.endpoint_policy, timeout=control.webhook_timeout)
    mail = partial(deliver_mail, runtime.storage, runtime.keys, SmtpMailer(settings.auth.mail))
    return Sweep(
        name="deliver_outbox",
        every=control.scan_seconds,
        run=Delivery(
            runtime.storage,
            {
                "webhook": webhooks,
                "child_result": child_results(runtime),
                "email": mail,
                MEMORY_PURGE: MemoryPurger(runtime),
                CLEANUP: partial(clean, runtime),
            },
            owner=new_object_id("ctl"),
            policies=settings.outbox.policies,
        ),
        timeout=2 * max(policy.lease_seconds for policy in settings.outbox.policies.values()),
    )


def _report_backlog(runtime: Runtime) -> Sweep:
    return Sweep(
        name="report_backlog",
        every=REPORT_SECONDS,
        run=BacklogReporter(runtime.storage, runtime.settings.outbox.policies),
        timeout=10,
    )


def _purge_outbox(runtime: Runtime) -> Sweep:
    config = runtime.settings.outbox
    return Sweep(
        name="purge_outbox",
        every=config.purge_interval_seconds,
        run=partial(
            purge_settled,
            runtime.storage,
            policies=config.policies,
            limit=config.purge_batch,
            budget_seconds=config.purge_budget_seconds,
        ),
        timeout=60,
    )


OSS = Distribution(
    name="oss",
    tables=(
        OrganizationRow,
        WorkspaceRow,
        WorkspaceProvisioningRow,
        PrincipalRow,
        PasswordRow,
        GrantRow,
        InvitationRow,
        ApiKeyRow,
        TokenRow,
        AuditEventRow,
        OutboxRow,
        ModelProviderRow,
        ModelProviderOAuthRow,
        ModelRow,
        WebProviderRow,
        ConnectorProviderRow,
        EnvironmentProviderRow,
        EnvironmentTemplateRow,
        ConnectionRow,
        SubscriptionRow,
        UploadRow,
        AssetRow,
        SkillRow,
        SkillRevisionRow,
        AgentRow,
        AgentRevisionRow,
        MemoryProviderRow,
        MemoryRow,
        MemoryFileStoreRow,
        MemoryFileRow,
        MemoryFileRevisionRow,
        SessionRow,
        ThreadRow,
        EnvironmentRow,
        ThreadEnvironmentRow,
        ThreadMemoryRow,
        InboxEntryRow,
        RunRow,
        PendingAnswerRow,
        RunItemPageRow,
        AttemptRow,
        UsageRecordRow,
    ),
    routers=(
        tenancy_router,
        organization_router,
        member_router,
        providers_router,
        models_router,
        agents_router,
        uploads_router,
        assets_router,
        skills_router,
        subscriptions_router,
        runs_router,
        usage_router,
        traces_router,
        environment_templates_router,
        environments_router,
        memories_router,
        thread_memories_router,
        connections_router,
        connector_providers_router,
    ),
    migrations=(Path(__file__).parent / "migrations" / "versions",),
    sweeps=(
        _advance_threads,
        _expire_leases,
        _expire_credentials,
        maintenance_sweep,
        renewal_sweep,
        _recover_connection_operations,
        _deliver_outbox,
        _purge_outbox,
        _report_backlog,
    ),
    providers=(
        *BUILT_IN_MODEL_PROVIDERS,
        *built_in_web_providers(),
        *BUILT_IN_CONNECTOR_PROVIDERS,
        *BUILT_IN_ENVIRONMENT_PROVIDERS,
        *BUILT_IN_MEMORY_PROVIDERS,
    ),
    roles=BUILT_IN_ROLES,
)
