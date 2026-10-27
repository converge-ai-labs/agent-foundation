"""Release-owned skills, described and packed once when the Service process starts."""

import hashlib
import posixpath
from collections.abc import Mapping
from pathlib import Path
from typing import get_args

from a13n_harness.environment.archives import FileArchive

from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import conflict, not_found
from a13n_service.resources.builtin_skills import BuiltinPackage, BuiltinSkill, BuiltinSkillName
from a13n_service.resources.skills import package
from a13n_service.resources.skills.schemas import BuiltinSkillDetail, SkillFile
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

PACKAGE_ROOT = Path(__file__).parents[2] / "builtin_skills"


async def _get_package(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    name: str,
    skills: Mapping[BuiltinSkillName, BuiltinPackage],
) -> BuiltinPackage:
    async with short_session(storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
    for key, bundled in skills.items():
        if key == name:
            return bundled
    raise not_found("builtin_skill", name)


async def get_skill(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    name: str,
    *,
    skills: Mapping[BuiltinSkillName, BuiltinPackage],
) -> BuiltinSkillDetail:
    bundled = await _get_package(storage, actor, workspace_id, name, skills)
    return BuiltinSkillDetail(
        name=bundled.definition.name,
        description=bundled.definition.description,
        root=bundled.archive.root,
        files=tuple(SkillFile(path=path, size=size) for path, size in bundled.archive.entries),
        package_digest=bundled.digest,
        package_size=len(bundled.archive.content),
    )


async def read_archive(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    name: str,
    *,
    skills: Mapping[BuiltinSkillName, BuiltinPackage],
    package_digest: str | None = None,
) -> bytes:
    bundled = await _get_package(storage, actor, workspace_id, name, skills)
    if package_digest is not None and package_digest != bundled.digest:
        raise conflict("builtin_skill", name, "package_changed")
    return bundled.archive.content


def load_packages(instance_catalog: bytes) -> dict[BuiltinSkillName, BuiltinPackage]:
    """Make portable packages with a release schema and this deployment's catalog.

    Host-mounted documents retain their existing paths. Copies for user environments use relative
    references, so shared and content-addressed directories need no additional Host mounts.
    """
    result = {}
    for name in get_args(BuiltinSkillName.__value__):
        root = PACKAGE_ROOT / name
        files = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            content = path.read_bytes()
            if path.suffix == ".md":
                instance = posixpath.relpath("instance", posixpath.dirname(relative) or ".")
                content = content.replace(b"/platform-instance", instance.encode())
            files[relative] = content
        files["instance/catalog.json"] = instance_catalog
        archive = package.pack(files)
        checked = package.read_package(archive)
        if checked.name != name:
            raise ValueError(f"Builtin skill directory and manifest disagree: {name}")
        result[name] = BuiltinPackage(
            definition=BuiltinSkill(name=name, description=checked.description),
            digest=hashlib.sha256(archive).hexdigest(),
            archive=FileArchive(archive, tuple((path, len(content)) for path, content in files.items())),
        )
    return result
