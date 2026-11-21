"""Expose existing Thread and Project operations to WebUI owner MCP clients."""

from collections.abc import Callable
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.http import StarletteWithLifespan
from mcp.types import ToolAnnotations
from pydantic import Field

from a13n_harness_ui.errors import HarnessUiError, ThreadError
from a13n_harness_ui.thread_capability import RemoteProjectRoot, ThreadToolController, thread_tool_failure

_PageLimit = Annotated[int, Field(ge=1, le=100)]
_ReadOnly = ToolAnnotations(read_only_hint=True)


def build_management_mcp(controller: Callable[[], ThreadToolController]) -> StarletteWithLifespan:
    server = FastMCP(
        "Harness UI",
        instructions="Manage Projects and Threads in this WebUI instance. Run receipts mean admission, not completion. "
        "Use get_thread to inspect saved results. Reconcile an uncertain write before retrying.",
        tasks=False,
        mask_error_details=True,
    )

    @server.tool(annotations=_ReadOnly)
    async def list_projects(
        query: str | None = None, cursor: str | None = None, limit: _PageLimit = 20
    ) -> dict[str, Any]:
        """Discover configured Projects by name or ID. Use get_project for roots and creation defaults."""
        try:
            return {
                "ok": True,
                **await controller().resources(kind="projects", query=query, cursor=cursor, limit=limit),
            }
        except HarnessUiError as exc:
            return thread_tool_failure(exc, "resource_list_failed")

    @server.tool(annotations=_ReadOnly)
    async def get_project(project_id: str) -> dict[str, Any]:
        """Inspect a configured Project's roots and creation defaults."""
        try:
            return {"ok": True, **await controller().get_project(project_id)}
        except HarnessUiError as exc:
            return thread_tool_failure(exc, "project_get_failed")

    @server.tool
    async def create_project(
        roots: Annotated[list[str], Field(max_length=64)] | None = None,
        name: Annotated[str, Field(min_length=1, max_length=256)] | None = None,
        remote_roots: Annotated[list[RemoteProjectRoot], Field(max_length=64)] | None = None,
        default_environment: str | None = None,
        environment_profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a Project from absolute local roots and/or remote Device directories; creates no Thread or Run.

        Name defaults to the first directory's name. Remote aliases are remote-1, remote-2, etc.
        Default is workspace with local roots, otherwise remote-1; default_environment overrides it.
        """
        try:
            return await controller().create_project(
                roots=roots,
                name=name,
                remote_roots=remote_roots,
                default_environment=default_environment,
                environment_profile_id=environment_profile_id,
            )
        except HarnessUiError as exc:
            return thread_tool_failure(exc, "project_creation_failed")

    @server.tool(annotations=_ReadOnly)
    async def list_threads(
        query: str | None = None,
        cursor: str | None = None,
        limit: _PageLimit = 20,
        project_id: str | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List root Threads, optionally filtered by Project, search text, or archive inclusion."""
        try:
            return {
                "ok": True,
                **await controller().list_threads(
                    source_thread_id=None,
                    query=query,
                    cursor=cursor,
                    limit=limit,
                    project_id=project_id,
                    include_archived=include_archived,
                ),
            }
        except (HarnessUiError, ValueError) as exc:
            return thread_tool_failure(exc, "thread_list_failed")

    @server.tool(annotations=_ReadOnly)
    async def get_thread(
        thread_id: str, history_cursor: str | None = None, history_limit: _PageLimit = 50
    ) -> dict[str, Any]:
        """Inspect current status, configuration and saved history for a Thread. Read saved output to verify results."""
        try:
            return {
                "ok": True,
                **await controller().get_thread(
                    source_thread_id=None,
                    thread_id=thread_id,
                    history_cursor=history_cursor,
                    history_limit=history_limit,
                ),
            }
        except (HarnessUiError, ValueError) as exc:
            return thread_tool_failure(exc, "thread_get_failed")

    @server.tool
    async def create_thread(
        prompt: str,
        title: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an independent root and submit its first prompt. Null project_id creates a projectless Thread.

        model_id overrides only the first Run. If admission fails after creation, keep the returned thread_id.
        """
        try:
            if not prompt.strip():
                raise ThreadError("A non-empty initial prompt is required.", code="thread_prompt_empty")
            return await controller().create_thread(
                source_thread_id=None,
                prompt=prompt,
                title=title,
                agent_id=agent_id,
                project_id=project_id,
                model_id=model_id,
            )
        except (HarnessUiError, ValueError) as exc:
            return thread_tool_failure(exc, "thread_create_failed")

    @server.tool
    async def send_thread_message(thread_id: str, message: str) -> dict[str, Any]:
        """Steer the current Run if active or start a new turn if idle. Returns acceptance, not completion.

        A rejected steer does not fall back to a new Run. Inspect rejected or uncertain sends before retrying.
        """
        try:
            if not message.strip():
                raise ThreadError("A non-empty message is required.", code="thread_message_empty")
            return await controller().send_thread_message(source_thread_id=None, thread_id=thread_id, message=message)
        except HarnessUiError as exc:
            return thread_tool_failure(exc, "thread_message_failed")

    # The parent WebUI AccessBoundary checks Host, Origin and the instance key before MCP dispatch.
    return server.http_app(path="/mcp", stateless_http=True, json_response=True, host_origin_protection=False)
