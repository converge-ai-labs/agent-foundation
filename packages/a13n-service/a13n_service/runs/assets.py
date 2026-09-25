"""The built-in `publish_asset` tool: a file of the run's environment becomes an asset of the run's workspace.

Publication takes the HTTP path under the run's authority: the file is staged as the run principal's upload
and the asset is created from it by the same service function, with the run, attempt and tool call recorded
as its source. Creating an asset needs `write` on the workspace within the run's delegated authority. The
upload's request key names the run, the tool call and the content digest, so repeating a call with the same
bytes returns the asset it created. A file is published whole, up to the largest object the store keeps.
"""

import hashlib
import mimetypes
import posixpath
from typing import Annotated

from a13n_harness import AgentContext
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_harness.tools import current_invocation_scope
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset

from a13n_service.resources.agents.toolsets import PUBLISH_ASSET_TOOL_ID
from a13n_service.resources.assets.schemas import AssetCreate
from a13n_service.resources.assets.service import create_asset
from a13n_service.resources.uploads import service as uploads
from a13n_service.runs.attempts import Lease
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tools import tool_failures
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, authorize


class AssetsCapability(AbstractCapability[AgentContext]):
    """The definition's `publish_asset` tool, bound to one attempt of the run."""

    id = "a13n.service.assets"

    def __init__(self, runtime: Runtime, lease: Lease, principal: Principal, authority: ExecutionAuthority):
        self.runtime, self.lease, self.principal, self.authority = runtime, lease, principal, authority

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        tool = HarnessTool(
            self.publish_asset,
            name="publish_asset",
            description="Publish a regular file from the environment as an immutable asset of the workspace.",
            harness_metadata=HarnessToolMetadata(
                tool_id=PUBLISH_ASSET_TOOL_ID,
                effects=frozenset({"read", "write"}),
                credential_audiences=(),
                idempotency="none",
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate", redact=True
                ),
            ),
        )
        return FunctionToolset(tools=[tool], id="a13n-service-assets")

    async def publish_asset(
        self,
        ctx: RunContext[AgentContext],
        path: Annotated[str, Field(min_length=1, max_length=4096, description="The file's environment path")],
        filename: Annotated[str | None, Field(max_length=255, description="Defaults to the file's name")] = None,
        media_type: Annotated[str | None, Field(max_length=127, description="Guessed from the name if omitted")] = None,
    ) -> dict[str, JsonValue]:
        call_id = current_invocation_scope().invocation.tool_call_id
        lease, runtime = self.lease, self.runtime
        scope = WorkspaceScope(lease.organization_id, lease.workspace_id)
        name = filename or posixpath.basename(path)
        with tool_failures():
            authorize(self.principal, scope, "write", authority=self.authority)
            content = await _read(ctx.deps.environment, path, runtime.settings.objects.max_bytes)
            receipt = await uploads.store(
                runtime.objects,
                scope,
                self.principal.id,
                request_key=f"run:{lease.run_id}:{call_id}:{hashlib.sha256(content).hexdigest()}",
                filename=name,
                content_type=media_type or mimetypes.guess_type(name)[0] or "application/octet-stream",
                content=content,
            )
            asset, _ = await create_asset(
                runtime.storage,
                runtime.objects,
                self.principal,
                scope.workspace_id,
                AssetCreate(upload_id=receipt.id, name=receipt.filename),
                source={"run_id": lease.run_id, "run_attempt_id": lease.attempt_id, "tool_call_id": call_id},
            )
        return {
            "asset_id": asset.id,
            "name": asset.name,
            "content_type": asset.content_type,
            "size": asset.size,
            "digest": asset.digest,
        }


async def _read(environment: BoundEnvironment, path: str, max_bytes: int) -> bytes:
    """The whole file, through one pinned route; larger files are refused before their bytes are held."""
    too_large = ToolFailed(f"The file exceeds the {max_bytes}-byte asset limit.")
    try:
        route = await environment.resolve_files(path)
        async with environment.open_files(route) as files:
            metadata = await files.stat(route.logical_path)
            if metadata.kind != "file":
                raise ToolFailed("Only a regular file can be published.")
            if metadata.size is not None and metadata.size > max_bytes:
                raise too_large
            content = bytearray()
            async for chunk in files.read_bytes_stream(route.logical_path):
                content += chunk
                if len(content) > max_bytes:
                    raise too_large
    except EnvironmentError as error:
        raise ToolFailed(str(error.safe_projection()["message"])) from None
    return bytes(content)
