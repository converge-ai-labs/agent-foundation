"""Verified deployment-only Skill bundles mounted through read-only Direct Local."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Literal

from a13n_environment import DirectLocalEnvironmentProvider, DirectLocalProviderRuntime, Environment
from a13n_harness.capabilities import FileSkillSource, SkillManager, SkillsCapability
from pydantic import Field

from a13n_service.digests import Sha256Digest, digest_request

from .context import BuiltinSkillBundleRef, StrictModel
from .definition import ASSETS
from .persistence import failure


class BundleManifest(StrictModel):
    schema_version: Literal["1"]
    bundle_id: str
    files: dict[str, Sha256Digest] = Field(min_length=1, max_length=128)


class KnowledgeBundles:
    def __init__(self, root: Path = ASSETS / "bundles") -> None:
        self._root = root.resolve()

    def verify(self, reference: BuiltinSkillBundleRef) -> Path:
        """No current-head lookup, network fetch, user directory discovery or writes."""
        try:
            root = self._root / reference.bundle_id
            if root.is_symlink() or not root.is_dir() or root.resolve().parent != self._root:
                raise ValueError("Bundle root is not confined.")
            manifest_path = root / "manifest.json"
            if manifest_path.is_symlink() or manifest_path.stat().st_size > 32 * 1024:
                raise ValueError("Bundle manifest is invalid.")
            manifest = BundleManifest.model_validate(json.loads(manifest_path.read_bytes()))
            if manifest.bundle_id != reference.bundle_id or digest_request(manifest) != reference.content_digest:
                raise ValueError("Bundle identity changed.")
            actual: set[str] = set()
            size = 0
            for path in root.rglob("*"):
                if path.is_symlink():
                    raise ValueError("Bundle symlinks are unsupported.")
                if path.is_file() and path != manifest_path:
                    actual.add(path.relative_to(root).as_posix())
            if actual != set(manifest.files) or "configure-agent/SKILL.md" not in actual:
                raise ValueError("Bundle file inventory changed.")
            for name, expected in manifest.files.items():
                logical = PurePosixPath(name)
                if (
                    logical.is_absolute()
                    or any(part in {".", ".."} for part in logical.parts)
                    or logical.as_posix() != name
                ):
                    raise ValueError("Bundle path is not canonical.")
                path = root / name
                if not path.resolve().is_relative_to(root):
                    raise ValueError("Bundle file escaped its root.")
                size += path.stat().st_size
                if size > 512 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError("Bundle file content changed.")
            return root
        except (OSError, ValueError, TypeError) as error:
            raise failure(
                "configuration_bundle_unavailable",
                "The exact accepted knowledge bundle is unavailable or incompatible.",
            ) from error

    def environment(self, reference: BuiltinSkillBundleRef) -> Environment:
        root = self.verify(reference)
        provider = DirectLocalEnvironmentProvider()
        configuration = provider.validate_configuration(
            schema_version="1", value={"root": {"path": str(root), "read_only": True}, "max_value_bytes": 512 * 1024}
        )
        return provider.create_environment(
            environment_id="configuration-knowledge",
            configuration=configuration,
            state=None,
            runtime=DirectLocalProviderRuntime(),
        )


def knowledge_capability() -> SkillsCapability:
    return SkillsCapability(SkillManager((FileSkillSource("configuration", ("/environment/builtin-skills",)),)))
