from pathlib import Path

import pytest
from a13n_service.skill_management.objects import SkillPackageStore, SkillPackageStoreError
from a13n_service.skill_management.package import normalize_skill_files, skill_package_object_key
from a13n_service.storage.object_store import LocalObjectStore

ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"


@pytest.mark.anyio
async def test_package_store_publishes_once_and_verifies_every_read(tmp_path: Path) -> None:
    objects = await LocalObjectStore.create(tmp_path / "objects")
    store = SkillPackageStore(objects)
    package = normalize_skill_files(
        (("SKILL.md", b"---\nname: deploy\ndescription: Deploy safely.\n---\n\n# Deploy\n"),)
    )

    await store.publish(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, package=package)
    await store.publish(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, package=package)

    assert (
        await store.read_verified(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            manifest=package.manifest,
        )
        == package.archive_bytes
    )


@pytest.mark.anyio
async def test_package_store_detects_tampering_without_exposing_internal_key(tmp_path: Path) -> None:
    objects = await LocalObjectStore.create(tmp_path / "objects")
    store = SkillPackageStore(objects)
    package = normalize_skill_files(
        (("SKILL.md", b"---\nname: deploy\ndescription: Deploy safely.\n---\n\n# Deploy\n"),)
    )
    await store.publish(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, package=package)
    key = skill_package_object_key(ORG_ID, WORKSPACE_ID, package.manifest.content_digest)
    await objects.put(key, b"not a ZIP")

    with pytest.raises(SkillPackageStoreError) as captured:
        await store.read_verified(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            manifest=package.manifest,
        )

    assert captured.value.code == "skill_package_invalid"
    assert key not in str(captured.value)
