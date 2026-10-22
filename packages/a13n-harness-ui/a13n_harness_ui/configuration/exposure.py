"""Translate built-in UI selections to their native Harness Toolset identities."""

from a13n_harness.tools.exposure import BUILTIN_TOOLSET_DESCRIPTIONS, ToolsetExposure

from a13n_harness_ui.errors import CompositionError

CAPABILITY_TOOLSETS: dict[str, tuple[str, ...]] = {
    "dynamic_environment": ("a13n-file-tools", "a13n-shell-tools", "a13n-computer-tools"),
    "web": ("a13n-web-tool-functions",),
    "documents": ("a13n-document-tool-functions",),
    "working_state": ("a13n-working-state-tools",),
    "user_interaction": ("a13n-user-interaction-tools",),
    "handoff": ("a13n-handoff-tools",),
    "codeact": ("codeact-state",),
}


def source_exposure(selection: ToolsetExposure, *, toolset_id: str | None = None) -> ToolsetExposure:
    return ToolsetExposure(
        exposure=selection.exposure,
        tool_exposure=selection.tool_exposure,
        description=selection.description or BUILTIN_TOOLSET_DESCRIPTIONS.get(toolset_id or ""),
    )


def capability_exposure(key: str, selection: ToolsetExposure) -> dict[str, ToolsetExposure]:
    native_ids = CAPABILITY_TOOLSETS.get(key, ())
    if not native_ids and (selection.exposure != "direct" or selection.tool_exposure):
        raise CompositionError(
            "Exposure on this Capability requires declared native Toolsets; use a Harness Plugin factory for custom tools.",
            code="capability_exposure_unavailable",
            details={"capability": key},
        )
    return {native_id: source_exposure(selection, toolset_id=native_id) for native_id in native_ids}
