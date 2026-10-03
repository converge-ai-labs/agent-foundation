"""Thread identity, metadata, and sticky configuration commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from a13n_harness import HarnessState
from a13n_harness import __version__ as harness_version
from anyio import CancelScope, to_thread

from a13n_harness_ui.composition import CompositionAcceptanceService
from a13n_harness_ui.configuration import LoadedHarnessUiConfiguration, ProjectDefaults, canonical_digest
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection, validate_environment_selection
from a13n_harness_ui.environment_profiles import FULL_CONTROL_PROFILE_ID, built_in_environment_profile
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.storage import (
    AgentResourceSource,
    LocalStore,
    ObjectKind,
    StoredContinuation,
    StoredThreadInitialState,
    Thread,
    ThreadConfiguration,
    ThreadConfigurationMutation,
)
from a13n_harness_ui.storage.contracts import MemoryAgentSource
from a13n_harness_ui.storage.read_models import project_continuation
from a13n_harness_ui.surfaces import (
    ConfigurationOrigin,
    ConfigurationProvenance,
    ProjectDefaultsPatch,
    ProjectDefaultsPreview,
    ThreadConfigurationResolution,
    ThreadMetadataMutation,
)


class _CreationDefault(Enum):
    global_default = "global_default"


@dataclass(frozen=True, slots=True)
class RootThreadDefaults:
    project_id: str | _CreationDefault | None = _CreationDefault.global_default
    agent_id: str | None = None
    default_model_id: str | None = None
    local_roots: tuple[str, ...] | None = None
    environment_profile_id: str | None = None
    environment_bindings: tuple[EnvironmentBindingSelection, ...] | None = None
    default_environment: str | _CreationDefault | None = _CreationDefault.global_default
    harness_plugin_ids: tuple[str, ...] | None = None
    environment_run_extension_ids: tuple[str, ...] | None = None
    mcp_server_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.environment_bindings is not None:
            object.__setattr__(
                self,
                "environment_bindings",
                tuple(EnvironmentBindingSelection.model_validate(item) for item in self.environment_bindings),
            )


class ThreadService:
    """Own Thread identity plus independent metadata and configuration heads."""

    def __init__(
        self,
        *,
        store: LocalStore,
        configurations: CompositionAcceptanceService,
    ) -> None:
        self._store = store
        self._configurations = configurations

    async def create(
        self,
        *,
        defaults: RootThreadDefaults | None = None,
        title: str | None = None,
        thread_id: str | None = None,
        coordinator_thread_id: str | None = None,
        coordinator: bool = False,
    ) -> Thread:
        source = await self._required_configuration()
        configuration = resolve_thread_configuration(source, defaults)
        if coordinator_thread_id is not None:
            owner = await self.require_interactive(coordinator_thread_id)
            if owner.archived:
                raise ThreadError("Restore the Coordinator before adding work.", code="thread_archived")
            if owner.configuration.project_id not in source.projects:
                raise ThreadError("The Coordinator's Project is unavailable.", code="thread_project_missing")
        if coordinator and configuration.project_id not in source.projects:
            raise ThreadError("A Coordinator requires an available Project.", code="thread_project_missing")
        return await self._create(
            configuration=configuration,
            title=title,
            thread_id=thread_id,
            coordinator_thread_id=coordinator_thread_id,
            coordinator=coordinator,
        )

    async def memory_thread(self, *, scope: str, project_id: str | None, model_id: str) -> Thread:
        """Create once under the organizer's cross-process scope lock."""
        existing = await self._store.threads.memory_thread(scope)
        if existing is not None:
            return existing
        return await self._create(
            configuration=ThreadConfiguration(
                version=1,
                project_id=project_id,
                agent_source=MemoryAgentSource(),
                default_model_id=model_id,
                environment_profile_id=FULL_CONTROL_PROFILE_ID,
            ),
            title="Memory",
            memory_scope=scope,
        )

    async def require_interactive(self, thread_id: str) -> Thread:
        thread = await self.get(thread_id)
        if thread.memory_scope is not None:
            raise ThreadError("Memory Threads are observation-only.", code="memory_thread_read_only")
        return thread

    async def promote_coordinator(self, thread_id: str) -> Thread:
        thread = await self.require_interactive(thread_id)
        if await self._store.threads.is_coordinator(thread_id):
            return thread
        source = await self._required_configuration()
        if thread.configuration.project_id not in source.projects:
            raise ThreadError("The selected Project is unavailable.", code="thread_project_missing")
        await self._store.threads.promote_coordinator(thread_id)
        return thread

    async def set_auto_followup(self, thread_id: str, auto_followup: bool) -> Thread:
        await self.require_interactive(thread_id)
        await self._store.threads.set_auto_followup(thread_id, auto_followup)
        return await self.get(thread_id)

    async def _create(
        self,
        *,
        configuration: ThreadConfiguration,
        title: str | None,
        thread_id: str | None = None,
        coordinator_thread_id: str | None = None,
        coordinator: bool = False,
        memory_scope: str | None = None,
    ) -> Thread:
        baseline = HarnessState.new(thread_id=thread_id)
        initial = await self._store.objects.publish_model(
            object_kind=ObjectKind.thread_initial_state,
            value=StoredThreadInitialState(harness_state=baseline, created_at=datetime.now(UTC)),
        )
        thread = await self._store.threads.create(
            thread_id=baseline.thread_id,
            memory_scope=memory_scope,
            configuration=configuration,
            initial_state=initial.ref,
            title=title,
            coordinator_thread_id=coordinator_thread_id,
            coordinator=coordinator,
        )
        await self._store.publish_work(thread.thread_id, initial.ref, baseline)
        return thread

    async def preview_creation(self, defaults: RootThreadDefaults | None = None) -> ThreadConfiguration:
        return resolve_thread_configuration(await self._required_configuration(), defaults)

    async def explain_creation(self, defaults: RootThreadDefaults | None = None) -> ThreadConfigurationResolution:
        return resolve_thread_configuration_details(await self._required_configuration(), defaults)

    async def preview_project_defaults(
        self, thread_id: str, *, environments_only: bool = False
    ) -> ProjectDefaultsPreview:
        thread = await self.require_interactive(thread_id)
        if thread.parent_thread_id is not None:
            raise ThreadError("Child Threads are managed through their parent execution.", code="child_thread_scoped")
        source = await self._required_configuration()
        project = source.projects.get(thread.configuration.project_id or "")
        if project is None:
            raise ThreadError("The Thread has no available Project.", code="thread_project_missing")
        if environments_only:
            patch = ProjectDefaultsPatch(
                local_roots=tuple(root.path for root in project.roots),
                environment_profile_id=(
                    project.defaults.environment_profile
                    or source.document.defaults.environment_profile
                    or FULL_CONTROL_PROFILE_ID
                ),
                environment_bindings=project.defaults.environment_bindings or (),
                default_environment=project.defaults.default_environment,
            )
        else:
            patch = project_defaults_patch(project.defaults)
        replacement = patch.apply(thread.configuration)
        _validate_configuration(source, replacement, root=True)
        return ProjectDefaultsPreview(
            thread_id=thread_id,
            project_id=project.id,
            defaults_digest=canonical_digest(patch if environments_only else project.defaults),
            expected_version=thread.configuration.version,
            patch=patch,
            current=thread.configuration,
            replacement=replacement,
        )

    async def apply_project_defaults(
        self, *, thread_id: str, expected_version: int, defaults_digest: str, environments_only: bool = False
    ) -> Thread:
        preview = await self.preview_project_defaults(thread_id, environments_only=environments_only)
        if preview.expected_version != expected_version or preview.defaults_digest != defaults_digest:
            raise ThreadError(
                "Project defaults or Thread configuration changed; preview again.", code="project_defaults_stale"
            )
        if preview.patch.is_empty:
            raise ThreadError("The Project specifies no defaults to apply.", code="project_defaults_empty")
        return await self.update_configuration(
            thread_id=thread_id,
            mutation=ThreadConfigurationMutation(expected_version=expected_version, patch=preview.patch),
        )

    async def get(self, thread_id: str) -> Thread:
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread does not exist.", code="thread_missing")
        return thread

    async def clear_context(self, *, thread_id: str, expected_continuation_id: str) -> None:
        """Replace root Agent state while retaining inspection history and Environment state.

        The App serializes this command with root admission. Immutable publication
        precedes the ordinary compare-and-select, so failures retain the old head.
        """
        thread = await self.require_interactive(thread_id)
        if thread.parent_thread_id is not None:
            raise ThreadError("Child Threads are managed through their parent execution.", code="child_thread_scoped")
        if thread.archived:
            raise ThreadError("Restore this conversation before clearing context.", code="thread_archived")
        if thread.continuation is None or thread.continuation.logical_digest != expected_continuation_id:
            raise ThreadError(
                "The conversation context changed. Review it before clearing context.",
                code="thread_continuation_conflict",
            )
        stored = await self._store.objects.read_model(thread.continuation, StoredContinuation)
        previous = stored.harness_state
        if previous.thread_id != thread_id:
            raise ThreadError("The selected state belongs to another Thread.", code="thread_continuation_incompatible")
        state = HarnessState.new(thread_id=thread_id, environment_states=previous.environment_states)
        replacement = StoredContinuation(
            harness_release=harness_version,
            run_composition=stored.run_composition,
            harness_state=state,
            display_history=stored.display_history,
            excerpt=stored.excerpt,
            created_at=datetime.now(UTC),
        )
        read_model = await to_thread.run_sync(project_continuation, replacement)
        with CancelScope(shield=True):
            published = await self._store.objects.publish_model(object_kind=ObjectKind.continuation, value=replacement)
            await self._store.threads.select_continuation(
                thread_id=thread_id,
                expected=thread.continuation,
                replacement=published.ref,
                read_model=read_model,
                activity_changed=False,
            )
            await self._store.publish_work(thread_id, published.ref, state)

    async def update_metadata(
        self,
        *,
        thread_id: str,
        mutation: ThreadMetadataMutation,
    ) -> Thread:
        thread = await self.require_interactive(thread_id)
        if thread.parent_thread_id is not None:
            raise ThreadError("Child Threads are managed through their parent execution.", code="child_thread_scoped")
        patch = mutation.patch
        if "starred" in patch.model_fields_set and await self._store.threads.worker_owners((thread_id,)):
            raise ThreadError("Star the Coordinator rather than its workers.", code="worker_star_unsupported")
        title = patch.title if "title" in patch.model_fields_set else thread.title
        archived = patch.archived if "archived" in patch.model_fields_set else thread.archived
        assert archived is not None
        return await self._store.threads.update_metadata(
            thread_id=thread_id,
            expected_version=mutation.expected_version,
            title=title,
            archived=archived,
            starred=patch.starred,
        )

    async def update_configuration(
        self,
        *,
        thread_id: str,
        mutation: ThreadConfigurationMutation,
    ) -> Thread:
        thread = await self.get(thread_id)
        if thread.configuration.version != mutation.expected_version:
            raise ThreadError(
                "Thread configuration version changed; read it again.", code="thread_configuration_conflict"
            )
        source = await self._required_configuration()
        replacement = mutation.patch.apply(thread.configuration)
        _validate_configuration(source, replacement, root=thread.parent_thread_id is None)
        return await self._store.threads.update_configuration(
            thread_id=thread_id,
            expected_version=mutation.expected_version,
            replacement=replacement,
        )

    async def _required_configuration(self) -> LoadedHarnessUiConfiguration:
        source = await self._configurations.current()
        if source is None:
            raise ThreadError(
                "No accepted Harness UI configuration is selected.",
                code="configuration_not_accepted",
            )
        return source


def project_defaults_patch(defaults: ProjectDefaults) -> ProjectDefaultsPatch:
    values: dict[str, object] = {}
    if defaults.agent is not None:
        values["agent_source"] = AgentResourceSource(id=defaults.agent)
    for name, value in (
        ("environment_profile_id", defaults.environment_profile),
        ("environment_bindings", defaults.environment_bindings),
        ("default_environment", defaults.default_environment),
        ("harness_plugin_ids", defaults.harness_plugins),
        ("environment_run_extension_ids", defaults.environment_run_extensions),
        ("mcp_server_ids", defaults.mcp_servers),
    ):
        if value is not None:
            values[name] = value
    return ProjectDefaultsPatch.model_validate(values)


def resolve_thread_configuration(
    source: LoadedHarnessUiConfiguration, defaults: RootThreadDefaults | None = None
) -> ThreadConfiguration:
    """Resolve every creation surface through the same ordered, per-axis defaults."""
    return resolve_thread_configuration_details(source, defaults).configuration


def resolve_thread_configuration_details(
    source: LoadedHarnessUiConfiguration, defaults: RootThreadDefaults | None = None
) -> ThreadConfigurationResolution:
    requested = defaults or RootThreadDefaults()
    global_defaults = source.document.defaults
    project_id = global_defaults.project if isinstance(requested.project_id, _CreationDefault) else requested.project_id
    if project_id is not None and project_id not in source.projects:
        raise ThreadError("The selected Project is unavailable.", code="thread_project_missing")
    project = ProjectDefaults() if project_id is None else source.projects[project_id].defaults
    agent_id, agent_origin = _first_selection(
        (requested.agent_id, "explicit"), (project.agent, "project"), (global_defaults.agent, "global")
    )
    if agent_id is None or agent_id not in source.agents:
        raise ThreadError("A root Thread requires an available Agent.", code="thread_agent_missing")
    agent = source.agents[agent_id]
    environment, environment_origin = _first_selection(
        (requested.environment_profile_id, "explicit"),
        (project.environment_profile, "project"),
        (global_defaults.environment_profile, "global"),
        (FULL_CONTROL_PROFILE_ID, "builtin"),
    )
    bindings, bindings_origin = _first_selection(
        (requested.environment_bindings, "explicit"), (project.environment_bindings, "project"), ((), "builtin")
    )
    if isinstance(requested.default_environment, _CreationDefault):
        default_environment, default_origin = _first_selection(
            (project.default_environment, "project"), (None, "builtin")
        )
    else:
        default_environment, default_origin = requested.default_environment, "explicit"
    plugins, plugins_origin = _first_selection(
        (requested.harness_plugin_ids, "explicit"),
        (project.harness_plugins, "project"),
        (agent.harness_plugins, "agent"),
        (global_defaults.harness_plugins, "global"),
    )
    extensions, extensions_origin = _first_selection(
        (requested.environment_run_extension_ids, "explicit"),
        (project.environment_run_extensions, "project"),
        (global_defaults.environment_run_extensions, "global"),
    )
    mcp, mcp_origin = _first_selection(
        (requested.mcp_server_ids, "explicit"),
        (project.mcp_servers, "project"),
        (agent.mcp_servers, "agent"),
        (global_defaults.mcp_servers, "global"),
    )
    assert environment is not None and plugins is not None and extensions is not None and mcp is not None
    roots, roots_origin = _first_selection(
        (requested.local_roots, "explicit"),
        (None if project_id is None else tuple(root.path for root in source.projects[project_id].roots), "project"),
        ((), "builtin"),
    )
    configuration = ThreadConfiguration(
        version=1,
        local_roots=roots or (),
        project_id=project_id,
        agent_source=AgentResourceSource(id=agent_id),
        default_model_id=requested.default_model_id,
        environment_profile_id=environment,
        environment_bindings=bindings or (),
        default_environment=default_environment,
        harness_plugin_ids=plugins,
        environment_run_extension_ids=extensions,
        mcp_server_ids=mcp,
    )
    _validate_configuration(source, configuration, root=True)
    return ThreadConfigurationResolution(
        configuration=configuration,
        provenance=ConfigurationProvenance(
            project_id="global" if isinstance(requested.project_id, _CreationDefault) else "explicit",
            agent_source=agent_origin,
            default_model_id="explicit" if requested.default_model_id is not None else "agent",
            local_roots=roots_origin,
            environment_profile_id=environment_origin,
            environment_bindings=bindings_origin,
            default_environment=default_origin,
            harness_plugin_ids=plugins_origin,
            environment_run_extension_ids=extensions_origin,
            mcp_server_ids=mcp_origin,
        ),
    )


def _first_selection[T](*candidates: tuple[T | None, ConfigurationOrigin]) -> tuple[T | None, ConfigurationOrigin]:
    for value, origin in candidates:
        if value is not None:
            return value, origin
    return None, candidates[-1][1]


def _validate_configuration(
    source: LoadedHarnessUiConfiguration,
    value: ThreadConfiguration,
    *,
    root: bool,
) -> None:
    if value.project_id is not None and value.project_id not in source.projects:
        raise ThreadError("The selected Project is unavailable.", code="thread_project_missing")
    if root and value.agent_source.kind != "agent":
        raise ThreadError("A root Thread cannot select a Markdown source.", code="thread_agent_invalid")
    if value.default_model_id is not None:
        if value.agent_source.kind != "agent":
            raise ThreadError("A Markdown child follows its parent Model.", code="thread_model_invalid")
        if value.default_model_id not in source.models:
            raise ThreadError("The selected Thread Model is unavailable.", code="thread_model_missing")
    for binding in value.environment_bindings:
        if binding.device_id not in source.devices:
            raise ThreadError("The selected Device is unavailable.", code="thread_device_missing")
    try:
        validate_environment_selection(
            value.environment_bindings,
            value.default_environment,
            local_root_count=len(value.local_roots),
        )
    except ValueError as error:
        raise ThreadError(str(error), code="thread_environment_invalid") from error
    resources = source.agents if value.agent_source.kind == "agent" else source.subagents
    if value.agent_source.id not in resources:
        raise ThreadError("The selected Agent source is unavailable.", code="thread_agent_missing")
    if (
        built_in_environment_profile(value.environment_profile_id) is None
        and value.environment_profile_id not in source.environment_profiles
    ):
        raise ThreadError(
            "The selected Environment profile is unavailable.",
            code="thread_environment_missing",
        )
    for selected, resources, code in (
        (value.harness_plugin_ids, source.harness_plugins, "thread_plugin_missing"),
        (
            value.environment_run_extension_ids,
            source.environment_run_extensions,
            "thread_run_extension_missing",
        ),
        (value.mcp_server_ids, source.mcp_servers, "thread_mcp_missing"),
    ):
        if any(item not in resources for item in selected):
            raise ThreadError("A selected Thread resource is unavailable.", code=code)


__all__ = ["RootThreadDefaults", "ThreadService"]
