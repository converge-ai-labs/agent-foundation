"""Immutable object-storage boundary for normalized managed Skill packages."""

from __future__ import annotations

import asyncio

from a13n_service.storage import ObjectConflict, ObjectNotFound, ObjectStore, ObjectStoreError

from .domain import ManagedSkillPackageManifest
from .package import (
    MAX_NORMALIZED_ARCHIVE_BYTES,
    NormalizedSkillPackage,
    SkillPackageError,
    normalize_stored_skill_zip,
    skill_package_object_key,
)


class SkillPackageStoreError(RuntimeError):
    """A package object is missing, unavailable, or violates its durable manifest."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillPackageStore:
    def __init__(self, objects: ObjectStore) -> None:
        self._objects = objects

    async def publish(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        package: NormalizedSkillPackage,
    ) -> None:
        key = skill_package_object_key(organization_id, workspace_id, package.manifest.content_digest)
        try:
            await self._objects.put(
                key,
                package.archive_bytes,
                content_type="application/zip",
                metadata={"content-digest": package.manifest.content_digest, "package-contract": "1"},
                if_none_match=True,
            )
            return
        except ObjectConflict:
            pass
        except ObjectStoreError as error:
            raise _unavailable() from error
        await self.read_verified(
            organization_id=organization_id,
            workspace_id=workspace_id,
            manifest=package.manifest,
        )

    async def read_verified(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        manifest: ManagedSkillPackageManifest,
    ) -> bytes:
        return (
            await self.read_verified_package(
                organization_id=organization_id,
                workspace_id=workspace_id,
                manifest=manifest,
            )
        ).archive_bytes

    async def read_verified_package(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        manifest: ManagedSkillPackageManifest,
    ) -> NormalizedSkillPackage:
        key = skill_package_object_key(organization_id, workspace_id, manifest.content_digest)
        body = bytearray()
        try:
            async with self._objects.open(key) as reader:
                async for chunk in reader:
                    body.extend(chunk)
                    if len(body) > MAX_NORMALIZED_ARCHIVE_BYTES:
                        raise SkillPackageStoreError(
                            "skill_package_invalid",
                            "The stored Skill package exceeds its contract limit.",
                        )
        except ObjectNotFound as error:
            raise SkillPackageStoreError(
                "skill_package_invalid",
                "The stored Skill package is missing.",
            ) from error
        except SkillPackageStoreError:
            raise
        except ObjectStoreError as error:
            raise _unavailable() from error

        try:
            normalized = await asyncio.to_thread(normalize_stored_skill_zip, bytes(body))
        except SkillPackageError as error:
            raise SkillPackageStoreError(
                "skill_package_invalid",
                "The stored Skill package is invalid.",
            ) from error
        if normalized.manifest != manifest:
            raise SkillPackageStoreError(
                "skill_package_invalid",
                "The stored Skill package does not match its revision manifest.",
            )
        return normalized


def _unavailable() -> SkillPackageStoreError:
    return SkillPackageStoreError(
        "skill_package_unavailable",
        "Skill package storage is temporarily unavailable.",
    )
