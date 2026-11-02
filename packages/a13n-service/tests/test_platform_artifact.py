"""Fixed artifact selection metadata must match every contributed platform tool contract."""

from dataclasses import replace

import pytest
from a13n_service import platform_artifact
from a13n_service.infra.platform import PlatformTool
from a13n_service.platform_tools import ToolContext, definition


async def inspect_extension(ctx: ToolContext, *, name: str) -> dict[str, str]:
    """Inspect an explicitly contributed extension resource."""
    return {"name": name}


CONTRACT = definition(inspect_extension, group="agents", effect="read")


@pytest.fixture
def bundled(monkeypatch):
    monkeypatch.setattr(
        platform_artifact,
        "PLATFORM_TOOLS",
        (PlatformTool(key=CONTRACT.name, group=CONTRACT.group, effect=CONTRACT.effect, summary=CONTRACT.description),),
    )


def test_matching_fixed_artifact_admits_its_explicit_extension(bundled):
    platform_artifact.validate_artifact_catalog((CONTRACT,))


def test_unbundled_extension_cannot_be_invoked_without_selectable_metadata(bundled):
    with pytest.raises(ValueError, match="unbundled definitions=\\['another_extension'\\]"):
        platform_artifact.validate_artifact_catalog((CONTRACT, replace(CONTRACT, name="another_extension")))


def test_removed_definition_cannot_leave_selectable_metadata(bundled):
    with pytest.raises(ValueError, match="missing definitions=\\['inspect_extension'\\]"):
        platform_artifact.validate_artifact_catalog(())


@pytest.mark.parametrize("change", [{"effect": "write"}, {"group": "models"}, {"description": "Different contract"}])
def test_permission_or_contract_drift_fails_assembly(bundled, change):
    with pytest.raises(ValueError, match="changed metadata=\\['inspect_extension'\\]"):
        platform_artifact.validate_artifact_catalog((replace(CONTRACT, **change),))
