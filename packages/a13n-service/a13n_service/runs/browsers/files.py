"""Workspace assets cross the private Browser filesystem under the active Run's authority."""

import json
import mimetypes
import posixpath
from dataclasses import dataclass
from typing import Literal

from a13n_harness.capabilities.browser import BrowserError
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend
from a13n_harness.tools import current_invocation_scope
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from a13n_service.infra.db import short_session, transaction
from a13n_service.providers.browser.definition import BrowserProvider, BrowserProviderError
from a13n_service.resources.assets import service as assets
from a13n_service.resources.assets.schemas import AssetCreate
from a13n_service.resources.uploads import service as uploads
from a13n_service.resources.uploads.schemas import FileName
from a13n_service.runs.attempts import Lease
from a13n_service.runs.browsers.tables import BrowserDownloadRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tool_executions.authority import lock_owner
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, authorize

_FILE_NAME = TypeAdapter(FileName)


@dataclass
class Files:
    runtime: Runtime
    lease: Lease
    browser_id: str
    generation: int
    provider: BrowserProvider
    state: dict[str, JsonValue]

    async def authorize(self, action: Literal["read", "write"], asset_ids: tuple[str, ...] = ()) -> Principal:
        async with transaction(self.runtime.storage) as session:
            run, principal = await lock_owner(session, self.runtime, self.lease)
            authorize(
                principal,
                WorkspaceScope(self.lease.organization_id, self.lease.workspace_id),
                action,
                authority=ExecutionAuthority.model_validate(run.authority),
            )
            await assets.require_usable(
                session, self.lease.workspace_id, {str(i): value for i, value in enumerate(asset_ids)}
            )
            return principal

    async def upload(self, native: CdpBrowserBackend, target: str, paths: tuple[str, ...], *, revision: str) -> str:
        if not paths or len(paths) > 16 or any(not path.startswith("asset:ast_") for path in paths):
            raise BrowserError("Upload paths must contain 1-16 workspace asset references: asset:<asset_id>.")
        asset_ids = tuple(path.removeprefix("asset:") for path in paths)
        principal = await self.authorize("read", asset_ids)
        staged = []
        for asset_id in asset_ids:
            asset = await assets.get_asset(self.runtime.storage, principal, self.lease.workspace_id, asset_id)
            if asset.size > self.runtime.settings.objects.max_bytes:
                raise BrowserError("The asset exceeds the Browser file size limit.")
            asset, content = await assets.read_asset_content(
                self.runtime.storage, self.runtime.objects, principal, self.lease.workspace_id, asset_id
            )
            await self.authorize("read", asset_ids)
            name = asset.name
            if name in {".", ".."} or any(c in name for c in ("/", "\\", "\x00")):
                name = asset_id
            staged.append(await self.provider.upload_file(self.state, name, content))
        await self.authorize("read", asset_ids)
        await native.upload(target, tuple(staged), revision=revision)
        return "Workspace asset files selected. Observe the form before submitting."

    async def downloads(self, native: CdpBrowserBackend) -> str:
        # Harness filters tracked records by this binding's exact target visibility. Host paths stay internal.
        report = json.loads(await native.downloads())
        for item in report["downloads"]:
            path = item.pop("path", None)
            if item["state"] != "completed":
                continue
            guid = posixpath.basename(path)
            await self.authorize("write")
            known = select(BrowserDownloadRow.upload_id).where(
                BrowserDownloadRow.browser_id == self.browser_id,
                BrowserDownloadRow.generation == self.generation,
                BrowserDownloadRow.guid == guid,
            )
            async with short_session(self.runtime.storage) as session:
                upload_id = await session.scalar(known)
            if upload_id is None:
                try:
                    content = await self.provider.download_file(
                        self.state, guid, max_bytes=self.runtime.settings.objects.max_bytes
                    )
                except BrowserProviderError as error:
                    if error.code != "download_too_large":
                        raise
                    item.update(state="failed", error="download_too_large")
                    continue
                principal = await self.authorize("write")
                name = posixpath.basename(item["name"].replace("\\", "/"))[:255] or "download"
                try:
                    name = _FILE_NAME.validate_python(name)
                except ValidationError:
                    name = "download"
                staged = await uploads.store(
                    self.runtime.storage,
                    self.runtime.objects,
                    WorkspaceScope(self.lease.organization_id, self.lease.workspace_id),
                    principal.id,
                    request_key=f"browser:{self.browser_id}:{self.generation}:{guid}",
                    filename=name,
                    content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                    content=content,
                )
                await self.authorize("write")
                async with transaction(self.runtime.storage) as session:
                    await session.execute(
                        insert(BrowserDownloadRow)
                        .values(
                            browser_id=self.browser_id,
                            generation=self.generation,
                            guid=guid,
                            upload_id=staged.upload_id,
                        )
                        .on_conflict_do_nothing()
                    )
                    upload_id = (await session.scalars(known)).one()
            async with short_session(self.runtime.storage) as session:
                upload = await uploads.find(session, self.lease.workspace_id, upload_id)
            principal = await self.authorize("write")
            asset, _ = await assets.create_asset(
                self.runtime.storage,
                principal,
                self.lease.workspace_id,
                AssetCreate(upload_id=upload.id, name=upload.filename),
                source={
                    "run_id": self.lease.run_id,
                    "run_attempt_id": self.lease.attempt_id,
                    "tool_call_id": current_invocation_scope().invocation.tool_call_id,
                },
            )
            item.update(
                asset_id=asset.id,
                name=asset.name,
                content_type=asset.content_type,
                size=asset.size,
                digest=asset.digest,
            )
        return json.dumps(report, ensure_ascii=False)
