"""Thread identity, metadata, and sticky configuration commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from a13n_harness import HarnessState

from a13n_ui.composition import CompositionAcceptanceService
from a13n_ui.configuration import LoadedAgentUiConfiguration
from a13n_ui.environment_profiles import FULL_CONTROL_PROFILE_ID, built_in_environment_profile
from a13n_ui.errors import ThreadError
from a13n_ui.storage import (
    AgentResourceSource,
    LocalStore,
    ObjectKind,
    StoredThreadInitialState,
    Thread,
    ThreadConfiguration,
    ThreadConfigurationMutation,
)
from a13n_ui.surfaces import ThreadMetadataMutation


@dataclass(frozen=True, slots=True)
class RootThreadDefaults:
    project_id: str | None = None
    agent_id: str | None = None
    environment_profile_id: str | None = None
    harness_plugin_ids: tuple[str, ...] | None = None
    environment_run_extension_ids: tuple[str, ...] | None = None
    mcp_server_ids: tuple[str, ...] | None = None


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
    ) -> Thread:
        source = await self._required_configuration()
        requested = defaults or RootThreadDefaults()
        project_id = requested.project_id or source.document.defaults.project
        agent_id = requested.agent_id or source.document.defaults.agent
        if project_id is None or project_id not in source.projects:
            raise ThreadError("A root Thread requires an available Project.", code="thread_project_missing")
        if agent_id is None or agent_id not in source.agents:
            raise ThreadError("A root Thread requires an available Agent.", code="thread_agent_missing")
        agent = source.agents[agent_id]
        environment_profile_id = (
            requested.environment_profile_id or source.document.defaults.environment_profile or FULL_CONTROL_PROFILE_ID
        )
        configuration = ThreadConfiguration(
            version=1,
            project_id=project_id,
            agent_source=AgentResourceSource(id=agent_id),
            environment_profile_id=environment_profile_id,
            harness_plugin_ids=(
                source.selected_plugins(agent) if requested.harness_plugin_ids is None else requested.harness_plugin_ids
            ),
            environment_run_extension_ids=(
                source.document.defaults.environment_run_extensions
                if requested.environment_run_extension_ids is None
                else requested.environment_run_extension_ids
            ),
            mcp_server_ids=(
                source.selected_mcp_servers(agent) if requested.mcp_server_ids is None else requested.mcp_server_ids
            ),
        )
        _validate_configuration(source, configuration, root=True)
        baseline = HarnessState.new()
        initial = await self._store.objects.publish_model(
            object_kind=ObjectKind.thread_initial_state,
            value=StoredThreadInitialState(harness_state=baseline, created_at=datetime.now(UTC)),
        )
        return await self._store.threads.create(
            thread_id=baseline.thread_id,
            configuration=configuration,
            initial_state=initial.ref,
            title=title,
        )

    async def get(self, thread_id: str) -> Thread:
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread does not exist.", code="thread_missing")
        return thread

    async def update_metadata(
        self,
        *,
        thread_id: str,
        mutation: ThreadMetadataMutation,
    ) -> Thread:
        thread = await self.get(thread_id)
        if thread.parent_thread_id is not None:
            raise ThreadError("Child Threads are managed through their parent execution.", code="child_thread_scoped")
        patch = mutation.patch
        title = patch.title if "title" in patch.model_fields_set else thread.title
        archived = patch.archived if "archived" in patch.model_fields_set else thread.archived
        assert archived is not None
        return await self._store.threads.update_metadata(
            thread_id=thread_id,
            expected_version=mutation.expected_version,
            title=title,
            archived=archived,
        )

    async def update_configuration(
        self,
        *,
        thread_id: str,
        mutation: ThreadConfigurationMutation,
    ) -> Thread:
        thread = await self.get(thread_id)
        source = await self._required_configuration()
        replacement = mutation.patch.apply(thread.configuration)
        _validate_configuration(source, replacement, root=thread.parent_thread_id is None)
        return await self._store.threads.update_configuration(
            thread_id=thread_id,
            expected_version=mutation.expected_version,
            replacement=replacement,
        )

    async def _required_configuration(self) -> LoadedAgentUiConfiguration:
        source = await self._configurations.current()
        if source is None:
            raise ThreadError(
                "No accepted Agent UI configuration is selected.",
                code="configuration_not_accepted",
            )
        return source


def _validate_configuration(
    source: LoadedAgentUiConfiguration,
    value: ThreadConfiguration,
    *,
    root: bool,
) -> None:
    if value.project_id not in source.projects:
        raise ThreadError("The selected Project is unavailable.", code="thread_project_missing")
    if root and value.agent_source.kind != "agent":
        raise ThreadError("A root Thread cannot select a Markdown source.", code="thread_agent_invalid")
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
