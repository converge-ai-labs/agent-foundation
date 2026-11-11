"""Immutable provider-neutral Environment values and failures."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer, field_validator, model_validator

from a13n_harness.environment._json import JsonBoundaryError, detach_json
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory, operation_error_projection, provider_error
from a13n_harness.providers.validation import PROVIDER_TYPE_PATTERN

type EnvironmentOperationFamily = Literal["files", "shell", "processes", "ports", "outputs", "state", "computer"]
ENVIRONMENT_ACTION_CATALOG_VERSION = "environment-actions/1"
DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS = 600.0
DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS = 600.0

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class EnvironmentError(Exception):
    """Stable provider-neutral Environment operation failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retry_hint: str | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self.code = code
        self.retry_hint = retry_hint
        self.details = dict(details or {})
        super().__init__(message)

    def safe_projection(self) -> dict[str, JsonValue]:
        """Return the common public failure value without local exception text."""
        return operation_error_projection(self.code, details=self.details, retry_hint=self.retry_hint)


class EnvironmentInvocationError(EnvironmentError):
    """The environment could not admit, route or deliver the requested tool call."""

    def safe_projection(self) -> dict[str, JsonValue]:
        return {**super().safe_projection(), "source": "system"}


class EnvironmentAction(StrEnum):
    """Exact authorization values in the locked first-party catalog."""

    FILE_STAT = "environment.file.stat"
    FILE_READ_TEXT = "environment.file.read_text"
    FILE_READ_BYTES = "environment.file.read_bytes"
    FILE_WRITE_TEXT = "environment.file.write_text"
    FILE_PATCH_TEXT = "environment.file.patch_text"
    FILE_LIST = "environment.file.list"
    FILE_QUERY = "environment.file.query"
    FILE_SEARCH_TEXT = "environment.file.search_text"
    FILE_MKDIR = "environment.file.mkdir"
    FILE_MOVE = "environment.file.move"
    FILE_REMOVE = "environment.file.remove"
    FILE_WRITE_BYTES = "environment.file.write_bytes"
    FILE_COPY_SOURCE = "environment.file.copy_source"
    FILE_COPY_DESTINATION = "environment.file.copy_destination"
    SHELL_EXEC = "environment.shell.exec"
    PROCESS_LIST = "environment.process.list"
    PROCESS_START = "environment.process.start"
    PROCESS_INSPECT = "environment.process.inspect"
    PROCESS_READ_OUTPUT = "environment.process.read_output"
    PROCESS_WRITE_STDIN = "environment.process.write_stdin"
    PROCESS_CLOSE_STDIN = "environment.process.close_stdin"
    PROCESS_SIGNAL = "environment.process.signal"
    PROCESS_WAIT = "environment.process.wait"
    PROCESS_KILL = "environment.process.kill"
    PROCESS_RELEASE = "environment.process.release"
    OUTPUT_READ = "environment.output.read"
    OUTPUT_RELEASE = "environment.output.release"
    PORT_INSPECT = "environment.port.inspect"
    PORT_WAIT = "environment.port.wait"
    STATE_EXPORT = "environment.state.export"
    STATE_RESTORE = "environment.state.restore"
    COMPUTER_DESCRIBE = "environment.computer.describe"
    COMPUTER_OBSERVE = "environment.computer.observe"
    COMPUTER_ZOOM = "environment.computer.zoom"
    COMPUTER_CLICK = "environment.computer.click"
    COMPUTER_MOVE = "environment.computer.move"
    COMPUTER_DRAG = "environment.computer.drag"
    COMPUTER_SCROLL = "environment.computer.scroll"
    COMPUTER_TYPE_TEXT = "environment.computer.type_text"
    COMPUTER_PRESS_KEYS = "environment.computer.press_keys"
    COMPUTER_READ_CLIPBOARD = "environment.computer.read_clipboard"
    COMPUTER_WRITE_CLIPBOARD = "environment.computer.write_clipboard"
    COMPUTER_A11Y_DESCRIBE = "environment.computer.a11y_describe"
    COMPUTER_A11Y_ROOTS = "environment.computer.a11y_roots"
    COMPUTER_A11Y_INSPECT = "environment.computer.a11y_inspect"
    COMPUTER_A11Y_FIND = "environment.computer.a11y_find"
    COMPUTER_A11Y_READ = "environment.computer.a11y_read"
    COMPUTER_A11Y_RANGE = "environment.computer.a11y_range"
    COMPUTER_A11Y_RANGE_READ = "environment.computer.a11y_range_read"
    COMPUTER_AX_DISCLOSING_SET = "environment.computer.ax_disclosing_set"
    COMPUTER_AX_EXPANDED_SET = "environment.computer.ax_expanded_set"
    COMPUTER_AX_FOCUS = "environment.computer.ax_focus"
    COMPUTER_AX_FOCUSED_WINDOW_SET = "environment.computer.ax_focused_window_set"
    COMPUTER_AX_MAIN_SET = "environment.computer.ax_main_set"
    COMPUTER_AX_MINIMIZED_SET = "environment.computer.ax_minimized_set"
    COMPUTER_AX_PERFORM_ACTION = "environment.computer.ax_perform_action"
    COMPUTER_AX_POSITION_SET = "environment.computer.ax_position_set"
    COMPUTER_AX_SELECTED_CHILDREN_SET = "environment.computer.ax_selected_children_set"
    COMPUTER_AX_SELECTED_COLUMNS_SET = "environment.computer.ax_selected_columns_set"
    COMPUTER_AX_SELECTED_ROWS_SET = "environment.computer.ax_selected_rows_set"
    COMPUTER_AX_SELECTED_SET = "environment.computer.ax_selected_set"
    COMPUTER_AX_SELECTED_TEXT_SET = "environment.computer.ax_selected_text_set"
    COMPUTER_AX_SIZE_SET = "environment.computer.ax_size_set"
    COMPUTER_AX_TEXT_RANGE_SELECT = "environment.computer.ax_text_range_select"
    COMPUTER_AX_TEXT_RANGES_SELECT = "environment.computer.ax_text_ranges_select"
    COMPUTER_AX_VALUE_NUMBER_SET = "environment.computer.ax_value_number_set"
    COMPUTER_AX_VALUE_STRING_SET = "environment.computer.ax_value_string_set"
    COMPUTER_AX_VISIBLE_TEXT_RANGE_SET = "environment.computer.ax_visible_text_range_set"
    COMPUTER_UIA_COLLAPSE = "environment.computer.uia_collapse"
    COMPUTER_UIA_DOCK_SET = "environment.computer.uia_dock_set"
    COMPUTER_UIA_EXPAND = "environment.computer.uia_expand"
    COMPUTER_UIA_FOCUS = "environment.computer.uia_focus"
    COMPUTER_UIA_INVOKE = "environment.computer.uia_invoke"
    COMPUTER_UIA_LEGACY_DEFAULT_ACTION = "environment.computer.uia_legacy_default_action"
    COMPUTER_UIA_LEGACY_SELECT = "environment.computer.uia_legacy_select"
    COMPUTER_UIA_LEGACY_VALUE_SET = "environment.computer.uia_legacy_value_set"
    COMPUTER_UIA_RANGE_ADD_SELECTION = "environment.computer.uia_range_add_selection"
    COMPUTER_UIA_RANGE_REMOVE_SELECTION = "environment.computer.uia_range_remove_selection"
    COMPUTER_UIA_RANGE_SCROLL_INTO_VIEW = "environment.computer.uia_range_scroll_into_view"
    COMPUTER_UIA_RANGE_SELECT = "environment.computer.uia_range_select"
    COMPUTER_UIA_RANGE_SHOW_CONTEXT_MENU = "environment.computer.uia_range_show_context_menu"
    COMPUTER_UIA_RANGE_VALUE_SET = "environment.computer.uia_range_value_set"
    COMPUTER_UIA_REALIZE = "environment.computer.uia_realize"
    COMPUTER_UIA_SCROLL_AMOUNT = "environment.computer.uia_scroll_amount"
    COMPUTER_UIA_SCROLL_INTO_VIEW = "environment.computer.uia_scroll_into_view"
    COMPUTER_UIA_SCROLL_PERCENT = "environment.computer.uia_scroll_percent"
    COMPUTER_UIA_SELECTION_ADD = "environment.computer.uia_selection_add"
    COMPUTER_UIA_SELECTION_REMOVE = "environment.computer.uia_selection_remove"
    COMPUTER_UIA_SELECTION_SELECT = "environment.computer.uia_selection_select"
    COMPUTER_UIA_SHOW_CONTEXT_MENU = "environment.computer.uia_show_context_menu"
    COMPUTER_UIA_TOGGLE = "environment.computer.uia_toggle"
    COMPUTER_UIA_TRANSFORM_MOVE = "environment.computer.uia_transform_move"
    COMPUTER_UIA_TRANSFORM_RESIZE = "environment.computer.uia_transform_resize"
    COMPUTER_UIA_TRANSFORM_ROTATE = "environment.computer.uia_transform_rotate"
    COMPUTER_UIA_TRANSFORM_ZOOM = "environment.computer.uia_transform_zoom"
    COMPUTER_UIA_TRANSFORM_ZOOM_UNIT = "environment.computer.uia_transform_zoom_unit"
    COMPUTER_UIA_VALUE_SET = "environment.computer.uia_value_set"
    COMPUTER_UIA_VIEW_SET = "environment.computer.uia_view_set"
    COMPUTER_UIA_WINDOW_CLOSE = "environment.computer.uia_window_close"
    COMPUTER_UIA_WINDOW_STATE_SET = "environment.computer.uia_window_state_set"
    COMPUTER_ATSPI_CARET_SET = "environment.computer.atspi_caret_set"
    COMPUTER_ATSPI_COMPONENT_EXTENTS_SET = "environment.computer.atspi_component_extents_set"
    COMPUTER_ATSPI_COMPONENT_POSITION_SET = "environment.computer.atspi_component_position_set"
    COMPUTER_ATSPI_COMPONENT_SIZE_SET = "environment.computer.atspi_component_size_set"
    COMPUTER_ATSPI_DO_ACTION = "environment.computer.atspi_do_action"
    COMPUTER_ATSPI_DOCUMENT_SELECTIONS_SET = "environment.computer.atspi_document_selections_set"
    COMPUTER_ATSPI_FOCUS = "environment.computer.atspi_focus"
    COMPUTER_ATSPI_RANGE_SCROLL_TO = "environment.computer.atspi_range_scroll_to"
    COMPUTER_ATSPI_RANGE_SCROLL_TO_POINT = "environment.computer.atspi_range_scroll_to_point"
    COMPUTER_ATSPI_SCROLL_TO = "environment.computer.atspi_scroll_to"
    COMPUTER_ATSPI_SCROLL_TO_POINT = "environment.computer.atspi_scroll_to_point"
    COMPUTER_ATSPI_SELECTION_ADD_CHILD = "environment.computer.atspi_selection_add_child"
    COMPUTER_ATSPI_SELECTION_ALL = "environment.computer.atspi_selection_all"
    COMPUTER_ATSPI_SELECTION_CLEAR = "environment.computer.atspi_selection_clear"
    COMPUTER_ATSPI_SELECTION_REMOVE_CHILD = "environment.computer.atspi_selection_remove_child"
    COMPUTER_ATSPI_SELECTION_REMOVE_SELECTED = "environment.computer.atspi_selection_remove_selected"
    COMPUTER_ATSPI_TABLE_COLUMN_SELECT = "environment.computer.atspi_table_column_select"
    COMPUTER_ATSPI_TABLE_COLUMN_UNSELECT = "environment.computer.atspi_table_column_unselect"
    COMPUTER_ATSPI_TABLE_ROW_SELECT = "environment.computer.atspi_table_row_select"
    COMPUTER_ATSPI_TABLE_ROW_UNSELECT = "environment.computer.atspi_table_row_unselect"
    COMPUTER_ATSPI_TEXT_CONTENTS_SET = "environment.computer.atspi_text_contents_set"
    COMPUTER_ATSPI_TEXT_COPY = "environment.computer.atspi_text_copy"
    COMPUTER_ATSPI_TEXT_CUT = "environment.computer.atspi_text_cut"
    COMPUTER_ATSPI_TEXT_DELETE = "environment.computer.atspi_text_delete"
    COMPUTER_ATSPI_TEXT_INSERT = "environment.computer.atspi_text_insert"
    COMPUTER_ATSPI_TEXT_PASTE = "environment.computer.atspi_text_paste"
    COMPUTER_ATSPI_TEXT_SELECTION_ADD = "environment.computer.atspi_text_selection_add"
    COMPUTER_ATSPI_TEXT_SELECTION_REMOVE = "environment.computer.atspi_text_selection_remove"
    COMPUTER_ATSPI_TEXT_SELECTION_SET = "environment.computer.atspi_text_selection_set"
    COMPUTER_ATSPI_VALUE_SET = "environment.computer.atspi_value_set"


COMPUTER_ACTIONS: frozenset[EnvironmentAction] = frozenset(
    action for action in EnvironmentAction if action.value.startswith("environment.computer.")
)
"""Shared desktop permissions; never included in legacy file/execution defaults."""

FILE_EXECUTION_ACTIONS: frozenset[EnvironmentAction] = frozenset(EnvironmentAction) - COMPUTER_ACTIONS

ACCESSIBILITY_READ_ACTIONS: frozenset[EnvironmentAction] = frozenset(
    action for action in EnvironmentAction if action.value.startswith("environment.computer.a11y_")
)
ACCESSIBILITY_MUTATION_ACTIONS: frozenset[EnvironmentAction] = frozenset(
    action
    for action in EnvironmentAction
    if action.value.startswith(("environment.computer.ax_", "environment.computer.uia_", "environment.computer.atspi_"))
)


FILE_ACTIONS: frozenset[EnvironmentAction] = frozenset(
    action for action in EnvironmentAction if action.value.startswith("environment.file.")
)
"""Every action in the `environment.file.*` family."""

FILE_READ_ACTIONS: frozenset[EnvironmentAction] = frozenset(
    {
        EnvironmentAction.FILE_STAT,
        EnvironmentAction.FILE_READ_TEXT,
        EnvironmentAction.FILE_READ_BYTES,
        EnvironmentAction.FILE_LIST,
        EnvironmentAction.FILE_QUERY,
        EnvironmentAction.FILE_SEARCH_TEXT,
        EnvironmentAction.FILE_COPY_SOURCE,
    }
)
"""File actions that only observe; the remaining `FILE_ACTIONS` mutate the filesystem."""


@dataclass(frozen=True, slots=True)
class EnvironmentActionDispatch:
    family: EnvironmentOperationFamily
    facet: str
    method: str


ENVIRONMENT_ACTION_DISPATCH: Mapping[EnvironmentAction, EnvironmentActionDispatch] = MappingProxyType(
    {
        EnvironmentAction.FILE_STAT: EnvironmentActionDispatch("files", "files", "stat"),
        EnvironmentAction.FILE_READ_TEXT: EnvironmentActionDispatch("files", "files", "read_text"),
        EnvironmentAction.FILE_READ_BYTES: EnvironmentActionDispatch("files", "files", "read_bytes"),
        EnvironmentAction.FILE_WRITE_TEXT: EnvironmentActionDispatch("files", "files", "write_text"),
        EnvironmentAction.FILE_PATCH_TEXT: EnvironmentActionDispatch("files", "files", "patch_text"),
        EnvironmentAction.FILE_LIST: EnvironmentActionDispatch("files", "files", "list"),
        EnvironmentAction.FILE_QUERY: EnvironmentActionDispatch("files", "files", "query"),
        EnvironmentAction.FILE_SEARCH_TEXT: EnvironmentActionDispatch("files", "files", "search_text"),
        EnvironmentAction.FILE_MKDIR: EnvironmentActionDispatch("files", "files", "mkdir"),
        EnvironmentAction.FILE_MOVE: EnvironmentActionDispatch("files", "files", "move"),
        EnvironmentAction.FILE_REMOVE: EnvironmentActionDispatch("files", "files", "remove"),
        EnvironmentAction.FILE_WRITE_BYTES: EnvironmentActionDispatch("files", "files", "write_bytes_stream"),
        EnvironmentAction.FILE_COPY_SOURCE: EnvironmentActionDispatch("files", "files", "copy"),
        EnvironmentAction.FILE_COPY_DESTINATION: EnvironmentActionDispatch("files", "files", "copy"),
        EnvironmentAction.SHELL_EXEC: EnvironmentActionDispatch("shell", "shell", "exec"),
        EnvironmentAction.PROCESS_LIST: EnvironmentActionDispatch("processes", "processes", "list"),
        EnvironmentAction.PROCESS_START: EnvironmentActionDispatch("processes", "processes", "start"),
        EnvironmentAction.PROCESS_INSPECT: EnvironmentActionDispatch("processes", "processes", "inspect"),
        EnvironmentAction.PROCESS_READ_OUTPUT: EnvironmentActionDispatch("processes", "processes", "read_output"),
        EnvironmentAction.PROCESS_WRITE_STDIN: EnvironmentActionDispatch("processes", "processes", "write_stdin"),
        EnvironmentAction.PROCESS_CLOSE_STDIN: EnvironmentActionDispatch("processes", "processes", "close_stdin"),
        EnvironmentAction.PROCESS_SIGNAL: EnvironmentActionDispatch("processes", "processes", "signal"),
        EnvironmentAction.PROCESS_WAIT: EnvironmentActionDispatch("processes", "processes", "wait"),
        EnvironmentAction.PROCESS_KILL: EnvironmentActionDispatch("processes", "processes", "kill"),
        EnvironmentAction.PROCESS_RELEASE: EnvironmentActionDispatch("processes", "processes", "release"),
        EnvironmentAction.OUTPUT_READ: EnvironmentActionDispatch("outputs", "outputs", "read"),
        EnvironmentAction.OUTPUT_RELEASE: EnvironmentActionDispatch("outputs", "outputs", "release"),
        EnvironmentAction.PORT_INSPECT: EnvironmentActionDispatch("ports", "ports", "inspect"),
        EnvironmentAction.PORT_WAIT: EnvironmentActionDispatch("ports", "ports", "wait"),
        EnvironmentAction.STATE_EXPORT: EnvironmentActionDispatch("state", "state", "export_state"),
        EnvironmentAction.STATE_RESTORE: EnvironmentActionDispatch("state", "state", "restore_state"),
        EnvironmentAction.COMPUTER_DESCRIBE: EnvironmentActionDispatch("computer", "computer", "describe"),
        EnvironmentAction.COMPUTER_OBSERVE: EnvironmentActionDispatch("computer", "computer", "observe"),
        EnvironmentAction.COMPUTER_ZOOM: EnvironmentActionDispatch("computer", "computer", "zoom"),
        EnvironmentAction.COMPUTER_CLICK: EnvironmentActionDispatch("computer", "computer", "execute"),
        EnvironmentAction.COMPUTER_MOVE: EnvironmentActionDispatch("computer", "computer", "execute"),
        EnvironmentAction.COMPUTER_DRAG: EnvironmentActionDispatch("computer", "computer", "execute"),
        EnvironmentAction.COMPUTER_SCROLL: EnvironmentActionDispatch("computer", "computer", "execute"),
        EnvironmentAction.COMPUTER_TYPE_TEXT: EnvironmentActionDispatch("computer", "computer", "execute"),
        EnvironmentAction.COMPUTER_PRESS_KEYS: EnvironmentActionDispatch("computer", "computer", "execute"),
        EnvironmentAction.COMPUTER_READ_CLIPBOARD: EnvironmentActionDispatch("computer", "computer", "read_clipboard"),
        EnvironmentAction.COMPUTER_WRITE_CLIPBOARD: EnvironmentActionDispatch(
            "computer", "computer", "write_clipboard"
        ),
        **{
            action: EnvironmentActionDispatch("computer", "accessibility", action.value.rsplit("a11y_", 1)[1])
            for action in ACCESSIBILITY_READ_ACTIONS
        },
        **{
            action: EnvironmentActionDispatch("computer", "accessibility", "execute")
            for action in ACCESSIBILITY_MUTATION_ACTIONS
        },
    }
)


def _require_identifier(value: str, name: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a bounded identifier")
    return value


def _freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})  # type: ignore[return-value]
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)  # type: ignore[return-value]
    return value


def _thaw_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw_json(item) for item in value]
    return value


class EnvironmentPermissionSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    operations: frozenset[EnvironmentAction] = frozenset()

    @field_serializer("operations", when_used="json")
    def serialize_operations(self, operations: frozenset[EnvironmentAction]) -> list[EnvironmentAction]:
        return sorted(operations)


class EnvironmentMountDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    path: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        return _require_identifier(value, "mount name")

    @field_validator("path")
    @classmethod
    def _valid_path(cls, value: str) -> str:
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("mount path must be an absolute path")
        return value


class EnvironmentDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    generation: str
    backing_identity: str | None = Field(default=None, min_length=1, max_length=256)
    environment_case_sensitive: bool = Field(default=True, exclude_if=lambda value: value)
    execution_boundary: Mapping[str, JsonValue] = Field(default_factory=dict)
    working_directory: str = Field(default="/", pattern=r"^/[^\x00]*$")
    operation_families: frozenset[EnvironmentOperationFamily]
    permissions: EnvironmentPermissionSet
    limits: Mapping[str, JsonValue] = Field(default_factory=dict)
    mounts: tuple[EnvironmentMountDescriptor, ...] = ()

    @field_validator("generation")
    @classmethod
    def _valid_generation(cls, value: str) -> str:
        return _require_identifier(value, "generation")

    @field_validator("limits", "execution_boundary", mode="after")
    @classmethod
    def _immutable_limits(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})

    @field_serializer("limits", "execution_boundary")
    def _serialize_limits(self, value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return {key: _thaw_json(item) for key, item in value.items()}

    @model_validator(mode="after")
    def _actions_match_families(self) -> EnvironmentDescriptor:
        missing = {
            ENVIRONMENT_ACTION_DISPATCH[action].family
            for action in self.permissions.operations
            if ENVIRONMENT_ACTION_DISPATCH[action].family not in self.operation_families
        }
        if missing:
            raise ValueError(f"permissions advertise absent operation families: {sorted(missing)}")
        return self


class EnvironmentAvailability(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["available", "preparing", "degraded", "unavailable"]
    ready_families: frozenset[EnvironmentOperationFamily] = frozenset()
    reason_code: str | None = None

    @field_validator("reason_code")
    @classmethod
    def _valid_reason(cls, value: str | None) -> str | None:
        return None if value is None else _require_identifier(value, "reason_code")


class EnvironmentProviderSpec(BaseModel):
    """Credential-free desired configuration for one Environment Provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: Annotated[
        str,
        Field(pattern=PROVIDER_TYPE_PATTERN),
    ]
    configuration: JsonValue

    @field_validator("configuration", mode="before")
    @classmethod
    def _finite_configuration(cls, value: object) -> JsonValue:
        try:
            return detach_json(value)
        except JsonBoundaryError as error:
            raise ValueError("configuration must be bounded finite JSON") from error


class EnvironmentState(BaseModel):
    """Provider-owned portable semantic soft reference for one target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: Annotated[
        str,
        Field(pattern=PROVIDER_TYPE_PATTERN),
    ]
    state_version: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    state: JsonValue

    @field_validator("state", mode="before")
    @classmethod
    def _finite_state(cls, value: object) -> JsonValue:
        try:
            return detach_json(value)
        except JsonBoundaryError as error:
            raise ValueError("state must be bounded finite JSON") from error


class EnvironmentTargetState(BaseModel):
    """Remembered Provider target, bound to the configuration that created it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: Annotated[str, Field(min_length=1, max_length=128)]
    configuration_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def decode_state_envelope[S: BaseModel](provider_key: str, state: EnvironmentState | None, model: type[S]) -> S:
    """Validate one Provider's own state envelope; absence is a caller decision."""
    if state is None or state.provider_key != provider_key or state.state_version != "1":
        raise provider_error(provider_key, "provider_state_invalid", EnvironmentProviderErrorCategory.INVALID)
    try:
        return model.model_validate(state.state)
    except ValueError:
        raise provider_error(provider_key, "provider_state_invalid", EnvironmentProviderErrorCategory.INVALID) from None


def decode_target_state[S: EnvironmentTargetState](
    provider_key: str, state: EnvironmentState | None, model: type[S], *, fingerprint: str
) -> S | None:
    """Decode a remembered managed target, refusing one built from another recipe."""
    if state is None:
        return None
    value = decode_state_envelope(provider_key, state, model)
    if value.configuration_fingerprint != fingerprint:
        raise provider_error(provider_key, "provider_target_conflict", EnvironmentProviderErrorCategory.CONFLICT)
    return value


class EnvironmentOperationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_id: str
    observed_generation: str
    operation_id: str
    stage: Literal["accepted", "dispatched", "exec_confirmed", "completed", "unknown"]
    outcome: Literal["succeeded", "failed", "cancelled", "timed_out", "unknown"] | None

    @field_validator("execution_id")
    @classmethod
    def _valid_execution_id(cls, value: str) -> str:
        return _require_identifier(value, "execution_id")
