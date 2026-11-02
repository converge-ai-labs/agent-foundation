"""Fixed artifact selection metadata must match every contributed business contract."""

import json
from dataclasses import replace
from importlib.resources import files

import pytest
from a13n_service import business_artifact
from a13n_service.business_tools import ToolContext, definition
from a13n_service.infra.platform import PlatformTool


async def inspect_extension(ctx: ToolContext, *, name: str) -> dict[str, str]:
    """Inspect an explicitly contributed extension resource."""
    return {"name": name}


CONTRACT = definition(inspect_extension, group="agents", effect="read")


@pytest.fixture
def bundled(monkeypatch):
    monkeypatch.setattr(
        business_artifact,
        "PLATFORM_TOOLS",
        (PlatformTool(key=CONTRACT.name, group=CONTRACT.group, effect=CONTRACT.effect, summary=CONTRACT.description),),
    )


def test_matching_fixed_artifact_admits_its_explicit_extension(bundled):
    business_artifact.validate_artifact_catalog((CONTRACT,))


def test_unbundled_extension_cannot_be_invoked_without_selectable_metadata(bundled):
    with pytest.raises(ValueError, match="unbundled definitions=\\['another_extension'\\]"):
        business_artifact.validate_artifact_catalog((CONTRACT, replace(CONTRACT, name="another_extension")))


def test_removed_definition_cannot_leave_selectable_metadata(bundled):
    with pytest.raises(ValueError, match="missing definitions=\\['inspect_extension'\\]"):
        business_artifact.validate_artifact_catalog(())


@pytest.mark.parametrize("change", [{"effect": "write"}, {"group": "models"}, {"description": "Different contract"}])
def test_permission_or_contract_drift_fails_assembly(bundled, change):
    with pytest.raises(ValueError, match="changed metadata=\\['inspect_extension'\\]"):
        business_artifact.validate_artifact_catalog((replace(CONTRACT, **change),))


def test_bundled_business_schemas_match_the_invoked_definitions():
    from a13n_service.business_catalog import catalog
    from a13n_service.infra.platform import PLATFORM_GROUPS

    artifact = files("a13n_service").joinpath("builtin_skills/platform-operations/schemas/business-tools.json")
    expected = {
        "tools": [
            {
                "name": tool.name,
                "group": tool.group,
                "effect": tool.effect,
                "description": tool.description,
                "input_schema": tool.inputs.model_json_schema(),
            }
            for tool in sorted(catalog(), key=lambda tool: (PLATFORM_GROUPS.index(tool.group), tool.name))
        ]
    }
    assert json.loads(artifact.read_text()) == expected
