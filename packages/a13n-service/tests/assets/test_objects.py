from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.assets.domain import Asset, UploadedAssetSource
from a13n_service.assets.objects import AssetObjectStore, asset_content_key
from a13n_service.assets.staging import AssetStaging
from a13n_service.iam.domain import PrincipalRef
from a13n_service.storage.object_store import ObjectNotFound, ObjectStore

ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
ASSET_ID = "ast_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"
CONTENT = b"asset-object-contract" * 8192


async def source(content: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(content), 8191):
        yield content[offset : offset + 8191]


@pytest.mark.anyio
async def test_asset_object_publication_verified_read_and_delete(
    object_store: ObjectStore,
    tmp_path: Path,
) -> None:
    staging = await AssetStaging.create(tmp_path / "files")
    objects = AssetObjectStore(object_store, staging)
    staged = await staging.stage_upload(
        source(CONTENT),
        max_size_bytes=len(CONTENT),
        content_length=None,
    )
    try:
        await objects.publish(
            asset_id=ASSET_ID,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            content=staged,
        )
    finally:
        await staged.remove()

    asset = Asset(
        id=ASSET_ID,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        filename="result.bin",
        media_type="application/octet-stream",
        size_bytes=len(CONTENT),
        content_sha256=hashlib.sha256(CONTENT).hexdigest(),
        source=UploadedAssetSource(principal=PrincipalRef(principal_type="user", principal_id=USER_ID)),
        created_at=datetime(2026, 8, 31, tzinfo=UTC),
        deleted_at=None,
    )
    prepared = await objects.prepare_verified_content(asset)
    try:
        assert b"".join([chunk async for chunk in prepared.chunks()]) == CONTENT
    finally:
        await prepared.remove()

    await objects.delete(asset)
    key = asset_content_key(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, asset_id=ASSET_ID)
    with pytest.raises(ObjectNotFound):
        await object_store.stat(key)
