"""Pure model-facing tool implementations composed by Harness Capabilities."""
# ruff: noqa: F401  # `_EXPORTS` owns the surface; these imports serve type checkers.

from typing import TYPE_CHECKING, Any

from a13n_harness._exports import exported_names, load_export

if TYPE_CHECKING:
    from .client import ClientToolsToolset
    from .codeact import CodeActPolicyToolset, CodeActToolPolicy
    from .context import HandoffToolset
    from .delegation import DelegateResult, DelegationToolset
    from .documents import DocumentsToolset
    from .file_media import (
        AUDIO_UNDERSTANDING_MODEL_ENV,
        AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV,
        IMAGE_UNDERSTANDING_MODEL_ENV,
        IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV,
        VIDEO_UNDERSTANDING_MODEL_ENV,
        VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV,
        AgentMediaUnderstandingProvider,
        MediaUnderstandingError,
        MediaUnderstandingProvider,
        MediaUnderstandingRequest,
        MediaUnderstandingResult,
        NativeInputMediaKind,
    )
    from .files import FILE_VIEW_RULES, FileToolset, FileViewRule
    from .interaction import UserInteractionToolset
    from .media import MediaToolset
    from .output import (
        DEFAULT_TOOL_OUTPUT_CHARS,
        FINAL_TOOL_OUTPUT_HARD_CHARS,
        MAX_TOOL_OUTPUT_SPILL_BYTES,
        ToolOutputDisclosure,
        acknowledge_tool_output,
        continuation_disclosure,
        create_tool_output_disclosure,
        disclose_mapping_field,
        disclose_sequence_field,
        disclose_text_fields,
        disclose_text_paths,
        fit_text_fields_to_limit,
        tool_output_bytes,
        tool_output_size,
        tool_output_text,
    )
    from .shell import ShellToolset
    from .subagents import AsyncSubagentToolset
    from .web import WebToolset
    from .working_state import WorkingStateToolset

_EXPORTS = {
    "a13n_harness.toolsets.client": ("ClientToolsToolset",),
    "a13n_harness.toolsets.codeact": (
        "CodeActPolicyToolset",
        "CodeActToolPolicy",
    ),
    "a13n_harness.toolsets.context": ("HandoffToolset",),
    "a13n_harness.toolsets.delegation": (
        "DelegateResult",
        "DelegationToolset",
    ),
    "a13n_harness.toolsets.documents": ("DocumentsToolset",),
    "a13n_harness.toolsets.file_media": (
        "AUDIO_UNDERSTANDING_MODEL_ENV",
        "AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV",
        "AgentMediaUnderstandingProvider",
        "IMAGE_UNDERSTANDING_MODEL_ENV",
        "IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV",
        "MediaUnderstandingError",
        "MediaUnderstandingProvider",
        "MediaUnderstandingRequest",
        "MediaUnderstandingResult",
        "NativeInputMediaKind",
        "VIDEO_UNDERSTANDING_MODEL_ENV",
        "VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV",
    ),
    "a13n_harness.toolsets.files": (
        "FILE_VIEW_RULES",
        "FileToolset",
        "FileViewRule",
    ),
    "a13n_harness.toolsets.interaction": ("UserInteractionToolset",),
    "a13n_harness.toolsets.media": ("MediaToolset",),
    "a13n_harness.toolsets.output": (
        "DEFAULT_TOOL_OUTPUT_CHARS",
        "FINAL_TOOL_OUTPUT_HARD_CHARS",
        "MAX_TOOL_OUTPUT_SPILL_BYTES",
        "ToolOutputDisclosure",
        "acknowledge_tool_output",
        "continuation_disclosure",
        "create_tool_output_disclosure",
        "disclose_mapping_field",
        "disclose_sequence_field",
        "disclose_text_fields",
        "disclose_text_paths",
        "fit_text_fields_to_limit",
        "tool_output_bytes",
        "tool_output_size",
        "tool_output_text",
    ),
    "a13n_harness.toolsets.shell": ("ShellToolset",),
    "a13n_harness.toolsets.subagents": ("AsyncSubagentToolset",),
    "a13n_harness.toolsets.web": ("WebToolset",),
    "a13n_harness.toolsets.working_state": ("WorkingStateToolset",),
}


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


__all__ = exported_names(_EXPORTS)  # pyright: ignore[reportUnsupportedDunderAll]
