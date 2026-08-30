from __future__ import annotations

from typing import TypeAliasType, get_args, get_origin, get_type_hints

from a13n_harness.toolsets.context import HandoffToolset
from a13n_harness.toolsets.documents import DocumentsToolset
from a13n_harness.toolsets.files import FileToolset
from a13n_harness.toolsets.media import MediaToolset
from a13n_harness.toolsets.shell import ShellToolset
from a13n_harness.toolsets.web import WebToolset
from a13n_harness.toolsets.working_state import WorkingStateToolset
from pydantic_ai import ToolReturn
from typing_extensions import is_typeddict


def _has_explicit_result_contract(annotation: object) -> bool:
    if isinstance(annotation, TypeAliasType):
        return _has_explicit_result_contract(annotation.__value__)
    if annotation in {str, None, type(None), ToolReturn}:
        return True
    if is_typeddict(annotation):
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    arguments = get_args(annotation)
    return bool(arguments) and all(_has_explicit_result_contract(argument) for argument in arguments)


def test_every_model_facing_function_tool_has_an_explicit_result_contract() -> None:
    surfaces = {
        FileToolset: {
            "view",
            "write",
            "edit",
            "multi_edit",
            "mkdir",
            "move",
            "copy",
            "delete",
            "ls",
            "glob",
            "grep",
        },
        ShellToolset: {
            "shell_exec",
            "shell_wait",
            "shell_status",
            "shell_input",
            "shell_signal",
            "shell_kill",
        },
        HandoffToolset: {"summarize"},
        WorkingStateToolset: {"task_create", "task_get", "task_list", "task_update", "note", "note_get"},
        MediaToolset: {"read_media"},
        DocumentsToolset: {"pdf_convert", "office_to_markdown"},
        WebToolset: {"search", "scrape", "fetch", "download"},
    }

    for toolset_type, names in surfaces.items():
        for name in names:
            annotation = get_type_hints(getattr(toolset_type, name), include_extras=True)["return"]
            assert _has_explicit_result_contract(annotation), f"{toolset_type.__name__}.{name}: {annotation!r}"
