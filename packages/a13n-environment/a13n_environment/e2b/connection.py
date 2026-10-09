"""Read-only E2B attachment using GET /sandboxes/{id}, never SDK connect()."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..errors import EnvironmentProviderErrorCategory as Category
from .errors import provider_error, sdk_errors

if TYPE_CHECKING:
    from e2b import AsyncSandbox
    from e2b.connection_config import ApiParams
    from e2b.sandbox.sandbox_api import SandboxInfo


async def open_sandbox(sandbox_id: str, options: ApiParams, validate: Callable[[SandboxInfo], None]) -> AsyncSandbox:
    # The SDK's high-level get_info projection drops the execution access token.
    # Its generated GET client preserves it without the resume/renew side effects
    # of AsyncSandbox.connect(). Keep this SDK-specific boundary in one module.
    from e2b import AsyncSandbox
    from e2b.api import handle_api_exception
    from e2b.api.client.api.sandboxes import get_sandboxes_sandbox_id
    from e2b.api.client.models.sandbox_detail import SandboxDetail
    from e2b.api.client_async import get_api_client
    from e2b.connection_config import ConnectionConfig
    from e2b.sandbox.sandbox_api import SandboxInfo
    from packaging.version import Version

    with sdk_errors():
        async with get_api_client(ConnectionConfig(**options)) as client:
            response = await get_sandboxes_sandbox_id.asyncio_detailed(sandbox_id, client=client)
        if response.status_code == 404:
            raise provider_error("provider_target_missing", Category.MISSING)
        if response.status_code >= 300:
            raise handle_api_exception(response)
        detail = response.parsed
        if not isinstance(detail, SandboxDetail):
            raise provider_error("provider_response_invalid", Category.UNAVAILABLE)
        info = SandboxInfo._from_sandbox_detail(detail)
        validate(info)
        if info.state.value != "running":
            raise provider_error("provider_target_stopped", Category.CONFLICT)
        if info.lifecycle is None or info.lifecycle.get("auto_resume") is not False:
            raise provider_error("provider_auto_resume_unsupported", Category.UNSUPPORTED)
        token = detail.envd_access_token
        if not isinstance(token, str) or not token:
            raise provider_error("provider_execution_token_missing", Category.UNSUPPORTED)
        headers = {
            "E2b-Sandbox-Id": sandbox_id,
            "E2b-Sandbox-Port": str(ConnectionConfig.envd_port),
            "X-Access-Token": token,
        }
        return AsyncSandbox(
            sandbox_id=sandbox_id,
            sandbox_domain=info.sandbox_domain,
            envd_version=Version(info.envd_version),
            envd_access_token=token,
            traffic_access_token=None,
            connection_config=ConnectionConfig(extra_sandbox_headers=headers, **options),
        )


async def close_sandbox(sandbox: AsyncSandbox) -> None:
    # The SDK has no public scope-close method. This releases its HTTP wrapper;
    # the SDK owns the shared pool and does not close it through this wrapper.
    await sandbox._envd_api.aclose()
