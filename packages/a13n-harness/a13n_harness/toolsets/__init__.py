"""Pure model-facing tool implementations composed by Harness Capabilities."""

from typing import TYPE_CHECKING, Any

from a13n_harness._exports import load_export

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
    "ClientToolsToolset": ("a13n_harness.toolsets.client", "ClientToolsToolset"),
    "CodeActPolicyToolset": ("a13n_harness.toolsets.codeact", "CodeActPolicyToolset"),
    "CodeActToolPolicy": ("a13n_harness.toolsets.codeact", "CodeActToolPolicy"),
    "HandoffToolset": ("a13n_harness.toolsets.context", "HandoffToolset"),
    "DelegateResult": ("a13n_harness.toolsets.delegation", "DelegateResult"),
    "DelegationToolset": ("a13n_harness.toolsets.delegation", "DelegationToolset"),
    "DocumentsToolset": ("a13n_harness.toolsets.documents", "DocumentsToolset"),
    "AUDIO_UNDERSTANDING_MODEL_ENV": ("a13n_harness.toolsets.file_media", "AUDIO_UNDERSTANDING_MODEL_ENV"),
    "AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV": (
        "a13n_harness.toolsets.file_media",
        "AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV",
    ),
    "IMAGE_UNDERSTANDING_MODEL_ENV": ("a13n_harness.toolsets.file_media", "IMAGE_UNDERSTANDING_MODEL_ENV"),
    "IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV": (
        "a13n_harness.toolsets.file_media",
        "IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV",
    ),
    "VIDEO_UNDERSTANDING_MODEL_ENV": ("a13n_harness.toolsets.file_media", "VIDEO_UNDERSTANDING_MODEL_ENV"),
    "VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV": (
        "a13n_harness.toolsets.file_media",
        "VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV",
    ),
    "AgentMediaUnderstandingProvider": ("a13n_harness.toolsets.file_media", "AgentMediaUnderstandingProvider"),
    "MediaUnderstandingError": ("a13n_harness.toolsets.file_media", "MediaUnderstandingError"),
    "MediaUnderstandingProvider": ("a13n_harness.toolsets.file_media", "MediaUnderstandingProvider"),
    "MediaUnderstandingRequest": ("a13n_harness.toolsets.file_media", "MediaUnderstandingRequest"),
    "MediaUnderstandingResult": ("a13n_harness.toolsets.file_media", "MediaUnderstandingResult"),
    "NativeInputMediaKind": ("a13n_harness.toolsets.file_media", "NativeInputMediaKind"),
    "FILE_VIEW_RULES": ("a13n_harness.toolsets.files", "FILE_VIEW_RULES"),
    "FileToolset": ("a13n_harness.toolsets.files", "FileToolset"),
    "FileViewRule": ("a13n_harness.toolsets.files", "FileViewRule"),
    "UserInteractionToolset": ("a13n_harness.toolsets.interaction", "UserInteractionToolset"),
    "MediaToolset": ("a13n_harness.toolsets.media", "MediaToolset"),
    "DEFAULT_TOOL_OUTPUT_CHARS": ("a13n_harness.toolsets.output", "DEFAULT_TOOL_OUTPUT_CHARS"),
    "FINAL_TOOL_OUTPUT_HARD_CHARS": ("a13n_harness.toolsets.output", "FINAL_TOOL_OUTPUT_HARD_CHARS"),
    "MAX_TOOL_OUTPUT_SPILL_BYTES": ("a13n_harness.toolsets.output", "MAX_TOOL_OUTPUT_SPILL_BYTES"),
    "ToolOutputDisclosure": ("a13n_harness.toolsets.output", "ToolOutputDisclosure"),
    "acknowledge_tool_output": ("a13n_harness.toolsets.output", "acknowledge_tool_output"),
    "continuation_disclosure": ("a13n_harness.toolsets.output", "continuation_disclosure"),
    "create_tool_output_disclosure": ("a13n_harness.toolsets.output", "create_tool_output_disclosure"),
    "disclose_mapping_field": ("a13n_harness.toolsets.output", "disclose_mapping_field"),
    "disclose_sequence_field": ("a13n_harness.toolsets.output", "disclose_sequence_field"),
    "disclose_text_fields": ("a13n_harness.toolsets.output", "disclose_text_fields"),
    "disclose_text_paths": ("a13n_harness.toolsets.output", "disclose_text_paths"),
    "fit_text_fields_to_limit": ("a13n_harness.toolsets.output", "fit_text_fields_to_limit"),
    "tool_output_bytes": ("a13n_harness.toolsets.output", "tool_output_bytes"),
    "tool_output_size": ("a13n_harness.toolsets.output", "tool_output_size"),
    "tool_output_text": ("a13n_harness.toolsets.output", "tool_output_text"),
    "ShellToolset": ("a13n_harness.toolsets.shell", "ShellToolset"),
    "AsyncSubagentToolset": ("a13n_harness.toolsets.subagents", "AsyncSubagentToolset"),
    "WebToolset": ("a13n_harness.toolsets.web", "WebToolset"),
    "WorkingStateToolset": ("a13n_harness.toolsets.working_state", "WorkingStateToolset"),
}


def __getattr__(name: str) -> Any:
    return load_export(__name__, globals(), _EXPORTS, name)


__all__ = [
    "AUDIO_UNDERSTANDING_MODEL_ENV",
    "AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV",
    "DEFAULT_TOOL_OUTPUT_CHARS",
    "FILE_VIEW_RULES",
    "FINAL_TOOL_OUTPUT_HARD_CHARS",
    "IMAGE_UNDERSTANDING_MODEL_ENV",
    "IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV",
    "MAX_TOOL_OUTPUT_SPILL_BYTES",
    "VIDEO_UNDERSTANDING_MODEL_ENV",
    "VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV",
    "AgentMediaUnderstandingProvider",
    "AsyncSubagentToolset",
    "ClientToolsToolset",
    "CodeActPolicyToolset",
    "CodeActToolPolicy",
    "DelegateResult",
    "DelegationToolset",
    "DocumentsToolset",
    "FileToolset",
    "FileViewRule",
    "HandoffToolset",
    "MediaToolset",
    "MediaUnderstandingError",
    "MediaUnderstandingProvider",
    "MediaUnderstandingRequest",
    "MediaUnderstandingResult",
    "NativeInputMediaKind",
    "ShellToolset",
    "ToolOutputDisclosure",
    "UserInteractionToolset",
    "WebToolset",
    "WorkingStateToolset",
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
]
