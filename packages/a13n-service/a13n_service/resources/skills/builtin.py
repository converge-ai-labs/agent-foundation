"""Release-owned skills, described and packed once when the Service process starts."""

import hashlib
import posixpath
from pathlib import Path
from typing import get_args

from a13n_harness.environment.archives import FileArchive

from a13n_service.resources.builtin_skills import BuiltinPackage, BuiltinSkill, BuiltinSkillName
from a13n_service.resources.skills import package

PACKAGE_ROOT = Path(__file__).parents[2] / "builtin_skills"


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
