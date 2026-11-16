"""Select release-owned guidance delivery without enabling tools or Skills."""

from functools import cache
from importlib.resources import files

from a13n_harness.environment import DynamicEnvironmentConfiguration
from a13n_harness.guidance import render_guidance

from a13n_harness_ui.composition.models import ResolvedAgentNode
from a13n_harness_ui.environment_paths import BUILTIN_SKILLS_PATH

GUIDANCE_SKILL_NAME = "harness-ui-operations"


def instruction_reference_root(node: ResolvedAgentNode) -> str | None:
    """Use references only when this Agent selects the bundle and its file reader."""
    if not any(item.capability == "skills" for item in node.capabilities):
        return None
    if node.tools is not None and "view" not in node.tools:
        return None
    for item in node.capabilities:
        if item.capability == "dynamic_environment":
            configuration = DynamicEnvironmentConfiguration.model_validate(item.configuration)
            if configuration.files_enabled and (configuration.file_tools is None or "view" in configuration.file_tools):
                return f"{BUILTIN_SKILLS_PATH}/{GUIDANCE_SKILL_NAME}"
    return None


@cache
def ui_guidance(name: str, reference_root: str | None = None) -> str:
    content = files("a13n_harness_ui").joinpath(f"assets/guidance/{name}.md").read_text(encoding="utf-8")
    path = f"{reference_root}/ui/{name}.md" if reference_root is not None else None
    return render_guidance(content, path)
