"""App-owned sharing authority over existing Projects, Threads and execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness_ui.acp_sessions import AcpSessionState
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_fast import FastState, fast_state
from a13n_harness_ui.model_thinking import summarize_thinking
from a13n_harness_ui.share_models import ShareAccess, ShareCreate, ShareCreated, ShareGrant, SharePermission, ShareScope
from a13n_harness_ui.storage.shares import ShareRepository, unavailable
from a13n_harness_ui.surfaces import NewThreadDefaults, ThreadDetail, ThreadModelDisplay, ThreadRole, ThreadSummary

if TYPE_CHECKING:
    from a13n_harness_ui.app import HarnessUiApp


def forbidden() -> HarnessUiError:
    return HarnessUiError("This action is not included in the sharing link.", code="share_forbidden")


class Sharing:
    def __init__(self, app: HarnessUiApp, repository: ShareRepository) -> None:
        self._app = app
        self._repository = repository

    async def _target(self, scope: ShareScope, target_id: str) -> tuple[str, ThreadRole | None]:
        if scope == "project":
            project = next((p for p in await self._app.projects() if p.project_id == target_id), None)
            if project is not None:
                return project.name, None
        else:
            try:
                detail = await self._app.get_thread(target_id)
            except HarnessUiError:
                raise unavailable() from None
            thread = detail.thread
            if (
                thread.parent_thread_id is None
                and thread.memory_scope is None
                and thread.configuration.agent_source.kind not in {"live", "bot"}
            ):
                return thread.title or "Untitled conversation", thread.role
        raise unavailable()

    async def create(self, command: ShareCreate) -> ShareCreated:
        await self._target(command.scope, command.target_id)
        return await self._repository.create(command)

    async def list(self, scope: ShareScope, target_id: str) -> tuple[ShareGrant, ...]:
        await self._target(scope, target_id)
        return await self._repository.list(scope, target_id)

    async def update(self, share_id: str, permission: SharePermission) -> ShareGrant:
        return await self._repository.update(share_id, permission)

    async def revoke(self, share_id: str) -> None:
        await self._repository.revoke(share_id)

    async def authenticate(self, share_id: str, key: str) -> ShareAccess:
        grant = await self._repository.authenticate(share_id, key)
        title, role = await self._target(grant.scope, grant.target_id)
        return ShareAccess(grant=grant, title=title, target_role=role)

    async def active(self, share_id: str, *, prompt: bool = False) -> ShareGrant:
        grant = await self._repository.active(share_id)
        await self._target(grant.scope, grant.target_id)
        if prompt and grant.permission != "prompt":
            raise forbidden()
        return grant

    async def thread(
        self, share_id: str, thread_id: str, *, prompt: bool = False, include_display: bool = False
    ) -> ThreadDetail:
        grant = await self.active(share_id, prompt=prompt)
        try:
            detail = await self._app.get_thread(thread_id)
            root = detail.thread
            while root.parent_thread_id is not None:
                root = (await self._app.get_thread(root.parent_thread_id)).thread
        except HarnessUiError:
            raise unavailable() from None
        if root.memory_scope is not None or root.configuration.agent_source.kind in {"live", "bot"}:
            raise unavailable()
        allowed = (
            grant.target_id in (root.thread_id, root.coordinator_thread_id)
            if grant.scope == "thread"
            else root.configuration.project_id == grant.target_id
        )
        if not allowed:
            raise unavailable()
        actions = tuple(
            action
            for action in detail.available_actions
            if action == "wait" or (grant.permission == "prompt" and action in {"run", "steer"})
        )
        thread = detail.thread
        if grant.scope == "thread" and thread.thread_id == grant.target_id and thread.coordinator_thread_id is not None:
            thread = thread.model_copy(update={"coordinator_thread_id": None})
        model_display = await self._model_display(thread_id, detail.thread) if include_display else None
        return detail.model_copy(
            update={"thread": thread, "available_actions": actions, "model_display": model_display}
        )

    async def _model_display(self, thread_id: str, thread: ThreadSummary) -> ThreadModelDisplay | None:
        source = await self._app.current_configuration()
        agent_source = thread.configuration.agent_source
        if agent_source.kind == "acp_agent":
            acp = source.acp_agents.get(agent_source.id) if source is not None else None
            agent_name = acp.name if acp is not None else agent_source.id
            preset = None if acp is None or acp.preset == "custom" else acp.preset
            raw = await self._app._store.threads.acp_state(thread_id)
            saved = AcpSessionState.model_validate_json(raw) if raw is not None else None
            selected_model = saved.options.get("model") if saved is not None else None
            selected_thinking = saved.options.get("thought_level") if saved is not None else None
            model_name = selected_model if isinstance(selected_model, str) and selected_model.strip() else None
            thinking_summary = (
                selected_thinking if isinstance(selected_thinking, str) and selected_thinking.strip() else None
            )
            return ThreadModelDisplay(
                agent_name=agent_name,
                model_name=model_name,
                model_identity=preset,
                thinking_summary=thinking_summary,
            )
        agent = source.agents.get(agent_source.id) if source is not None and agent_source.kind == "agent" else None
        agent_name = agent.name if agent is not None else agent_source.id
        captured_agent = None
        try:
            inspection = await self._app.inspect_thread_configuration(thread_id)
        except HarnessUiError:
            inspection = None
        if inspection is not None and inspection.captured is not None:
            captured_agent = inspection.captured.agent
        model_id = (
            (captured_agent.model_id if captured_agent is not None else None)
            or (inspection.next_model_id if inspection is not None else None)
            or thread.configuration.default_model_id
            or (agent.model if agent is not None else None)
        )
        model = source.models.get(model_id) if source is not None and model_id is not None else None
        thinking = (
            captured_agent.thinking_summary
            if captured_agent is not None
            else summarize_thinking(model.route, model.settings)
            if model is not None
            else None
        )
        if thinking == "Provider default":
            thinking = None
        fast: FastState = (
            captured_agent.fast
            if captured_agent is not None
            else fast_state(model.route, model.settings)
            if model is not None
            else "default"
        )
        return ThreadModelDisplay(
            agent_name=agent_name,
            model_name=model.name if model is not None else model_id,
            model_identity=(model.base_model or model.route) if model is not None else None,
            thinking_summary=thinking,
            fast=fast,
        )

    async def create_thread(self, share_id: str, *, thread_id: str, title: str | None) -> ThreadSummary:
        grant = await self.active(share_id, prompt=True)
        if grant.scope != "project":
            raise forbidden()
        return await self._app.create_thread(
            defaults=NewThreadDefaults(project_id=grant.target_id), thread_id=thread_id, title=title
        )
