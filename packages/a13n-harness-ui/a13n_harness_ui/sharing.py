"""App-owned sharing authority over existing Projects, Threads and execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.share_models import ShareAccess, ShareCreate, ShareCreated, ShareGrant, SharePermission, ShareScope
from a13n_harness_ui.storage.shares import ShareRepository, unavailable
from a13n_harness_ui.surfaces import NewThreadDefaults, ThreadDetail, ThreadSummary

if TYPE_CHECKING:
    from a13n_harness_ui.app import HarnessUiApp


def forbidden() -> HarnessUiError:
    return HarnessUiError("This action is not included in the sharing link.", code="share_forbidden")


class Sharing:
    def __init__(self, app: HarnessUiApp, repository: ShareRepository) -> None:
        self._app = app
        self._repository = repository

    async def _target_title(self, scope: ShareScope, target_id: str) -> str:
        if scope == "project":
            project = next((p for p in await self._app.projects() if p.project_id == target_id), None)
            if project is not None:
                return project.name
        else:
            try:
                detail = await self._app.get_thread(target_id)
            except HarnessUiError:
                raise unavailable() from None
            thread = detail.thread
            if (
                thread.parent_thread_id is None
                and thread.memory_scope is None
                and thread.configuration.agent_source.kind != "live"
            ):
                return thread.title or "Untitled conversation"
        raise unavailable()

    async def create(self, command: ShareCreate) -> ShareCreated:
        await self._target_title(command.scope, command.target_id)
        return await self._repository.create(command)

    async def list(self, scope: ShareScope, target_id: str) -> tuple[ShareGrant, ...]:
        await self._target_title(scope, target_id)
        return await self._repository.list(scope, target_id)

    async def update(self, share_id: str, permission: SharePermission) -> ShareGrant:
        return await self._repository.update(share_id, permission)

    async def revoke(self, share_id: str) -> None:
        await self._repository.revoke(share_id)

    async def authenticate(self, share_id: str, key: str) -> ShareAccess:
        grant = await self._repository.authenticate(share_id, key)
        return ShareAccess(grant=grant, title=await self._target_title(grant.scope, grant.target_id))

    async def active(self, share_id: str, *, prompt: bool = False) -> ShareGrant:
        grant = await self._repository.active(share_id)
        await self._target_title(grant.scope, grant.target_id)
        if prompt and grant.permission != "prompt":
            raise forbidden()
        return grant

    async def thread(self, share_id: str, thread_id: str, *, prompt: bool = False) -> ThreadDetail:
        grant = await self.active(share_id, prompt=prompt)
        try:
            detail = await self._app.get_thread(thread_id)
            root = detail.thread
            while root.parent_thread_id is not None:
                root = (await self._app.get_thread(root.parent_thread_id)).thread
        except HarnessUiError:
            raise unavailable() from None
        if root.memory_scope is not None or root.configuration.agent_source.kind == "live":
            raise unavailable()
        allowed = (
            root.thread_id == grant.target_id
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
        return detail.model_copy(update={"available_actions": actions})

    async def create_thread(self, share_id: str, *, thread_id: str, title: str | None) -> ThreadSummary:
        grant = await self.active(share_id, prompt=True)
        if grant.scope != "project":
            raise forbidden()
        return await self._app.create_thread(
            defaults=NewThreadDefaults(project_id=grant.target_id), thread_id=thread_id, title=title
        )
