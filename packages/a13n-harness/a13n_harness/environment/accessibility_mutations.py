"""Closed native semantic operations; each op maps to one exact Computer action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from a13n_harness.environment.accessibility import (
    AccessibilityActionReference,
    AccessibilityModel,
    AccessibilityNode,
    AccessibilityNodeReference,
    AccessibilityRangeReference,
    AccessibilityText,
    AtspiDocumentSelection,
    NativePoint,
)
from a13n_harness.environment.models import EnvironmentOperationReceipt


class AXAction(AccessibilityModel):
    op: Literal["perform_action"] = "perform_action"
    action: AccessibilityActionReference


class AXFocus(AccessibilityModel):
    op: Literal["focus"] = "focus"


class AXStringValue(AccessibilityModel):
    op: Literal["value_string_set"] = "value_string_set"
    value: AccessibilityText


class AXNumberValue(AccessibilityModel):
    op: Literal["value_number_set"] = "value_number_set"
    value: FiniteFloat


class AXSelectedText(AccessibilityModel):
    op: Literal["selected_text_set"] = "selected_text_set"
    text: AccessibilityText


class AXRange(AccessibilityModel):
    op: Literal["text_range_select", "visible_text_range_set"]
    range: AccessibilityRangeReference


class AXRanges(AccessibilityModel):
    op: Literal["text_ranges_select"] = "text_ranges_select"
    ranges: tuple[AccessibilityRangeReference, ...] = Field(max_length=256)


class AXChildren(AccessibilityModel):
    op: Literal["selected_children_set", "selected_rows_set", "selected_columns_set"]
    children: tuple[AccessibilityNodeReference, ...] = Field(max_length=512)


class AXBoolean(AccessibilityModel):
    op: Literal["selected_set", "expanded_set", "disclosing_set", "minimized_set", "main_set"]
    value: bool


class AXPosition(AccessibilityModel):
    op: Literal["position_set"] = "position_set"
    x: FiniteFloat
    y: FiniteFloat


class AXSize(AccessibilityModel):
    op: Literal["size_set"] = "size_set"
    width: FiniteFloat = Field(gt=0)
    height: FiniteFloat = Field(gt=0)


class AXFocusedWindow(AccessibilityModel):
    op: Literal["focused_window_set"] = "focused_window_set"
    window: AccessibilityNodeReference


type AXMutation = Annotated[
    AXAction
    | AXFocus
    | AXStringValue
    | AXNumberValue
    | AXSelectedText
    | AXRange
    | AXRanges
    | AXChildren
    | AXBoolean
    | AXPosition
    | AXSize
    | AXFocusedWindow,
    Field(discriminator="op"),
]


class UIACommand(AccessibilityModel):
    op: Literal[
        "focus",
        "show_context_menu",
        "invoke",
        "toggle",
        "expand",
        "collapse",
        "selection_select",
        "selection_add",
        "selection_remove",
        "scroll_into_view",
        "window_close",
        "realize",
        "legacy_default_action",
    ]


class UIAStringValue(AccessibilityModel):
    op: Literal["value_set", "legacy_value_set"]
    value: AccessibilityText


class UIANumberValue(AccessibilityModel):
    op: Literal["range_value_set"] = "range_value_set"
    value: FiniteFloat


type ScrollAmount = Literal["large_decrement", "small_decrement", "no_amount", "large_increment", "small_increment"]


class UIAScrollAmount(AccessibilityModel):
    op: Literal["scroll_amount"] = "scroll_amount"
    horizontal: ScrollAmount = "no_amount"
    vertical: ScrollAmount = "no_amount"


class UIAScrollPercent(AccessibilityModel):
    op: Literal["scroll_percent"] = "scroll_percent"
    horizontal: FiniteFloat | None = Field(default=None, ge=0, le=100)
    vertical: FiniteFloat | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _axis(self) -> Self:
        if self.horizontal is None and self.vertical is None:
            raise ValueError("Scroll percent requires at least one axis.")
        return self


class UIARange(AccessibilityModel):
    op: Literal["range_select", "range_add_selection", "range_remove_selection", "range_show_context_menu"]
    range: AccessibilityRangeReference


class UIARangeScroll(AccessibilityModel):
    op: Literal["range_scroll_into_view"] = "range_scroll_into_view"
    range: AccessibilityRangeReference
    align_top: bool = True


class UIAWindowState(AccessibilityModel):
    op: Literal["window_state_set"] = "window_state_set"
    state: Literal["normal", "maximized", "minimized"]


class UIAMove(AccessibilityModel):
    op: Literal["transform_move"] = "transform_move"
    x: FiniteFloat
    y: FiniteFloat


class UIAResize(AccessibilityModel):
    op: Literal["transform_resize"] = "transform_resize"
    width: FiniteFloat = Field(gt=0)
    height: FiniteFloat = Field(gt=0)


class UIARotate(AccessibilityModel):
    op: Literal["transform_rotate"] = "transform_rotate"
    degrees: FiniteFloat


class UIAZoom(AccessibilityModel):
    op: Literal["transform_zoom"] = "transform_zoom"
    percent: FiniteFloat = Field(gt=0)


class UIAZoomUnit(AccessibilityModel):
    op: Literal["transform_zoom_unit"] = "transform_zoom_unit"
    unit: Literal["no_amount", "large_decrement", "small_decrement", "large_increment", "small_increment"]


class UIADock(AccessibilityModel):
    op: Literal["dock_set"] = "dock_set"
    position: Literal["top", "left", "bottom", "right", "fill", "none"]


class UIAView(AccessibilityModel):
    op: Literal["view_set"] = "view_set"
    view_id: int


class UIALegacySelect(AccessibilityModel):
    op: Literal["legacy_select"] = "legacy_select"
    selection: Literal["none", "take", "extend", "add", "remove"] = "none"
    take_focus: bool = False


type UIAMutation = Annotated[
    UIACommand
    | UIAStringValue
    | UIANumberValue
    | UIAScrollAmount
    | UIAScrollPercent
    | UIARange
    | UIARangeScroll
    | UIAWindowState
    | UIAMove
    | UIAResize
    | UIARotate
    | UIAZoom
    | UIAZoomUnit
    | UIADock
    | UIAView
    | UIALegacySelect,
    Field(discriminator="op"),
]


class AtspiAction(AccessibilityModel):
    op: Literal["do_action"] = "do_action"
    action: AccessibilityActionReference


class AtspiCommand(AccessibilityModel):
    op: Literal["focus", "selection_all", "selection_clear"]


class AtspiValue(AccessibilityModel):
    op: Literal["value_set"] = "value_set"
    value: FiniteFloat


class AtspiChildSelection(AccessibilityModel):
    op: Literal["selection_add_child", "selection_remove_child", "selection_remove_selected"]
    child: AccessibilityNodeReference


class AtspiTextContents(AccessibilityModel):
    op: Literal["text_contents_set"] = "text_contents_set"
    text: AccessibilityText


class AtspiTextInsert(AccessibilityModel):
    op: Literal["text_insert"] = "text_insert"
    position: int = Field(ge=0)
    text: AccessibilityText


class AtspiTextRange(AccessibilityModel):
    op: Literal["text_delete", "text_copy", "text_cut", "text_selection_add"]
    range: AccessibilityRangeReference


class AtspiTextPosition(AccessibilityModel):
    op: Literal["text_paste", "caret_set"]
    position: int = Field(ge=0)


class AtspiSelectionSet(AccessibilityModel):
    op: Literal["text_selection_set"] = "text_selection_set"
    index: int = Field(ge=0)
    range: AccessibilityRangeReference


class AtspiSelectionRemove(AccessibilityModel):
    op: Literal["text_selection_remove"] = "text_selection_remove"
    index: int = Field(ge=0)


class AtspiDocumentSelections(AccessibilityModel):
    op: Literal["document_selections_set"] = "document_selections_set"
    selections: tuple[AtspiDocumentSelection, ...] = Field(max_length=256)


type AtspiScrollType = Literal[
    "top_left", "bottom_right", "top_edge", "bottom_edge", "left_edge", "right_edge", "anywhere"
]


class AtspiScroll(AccessibilityModel):
    op: Literal["scroll_to"] = "scroll_to"
    alignment: AtspiScrollType


class AtspiScrollPoint(AccessibilityModel):
    op: Literal["scroll_to_point", "component_position_set"]
    point: NativePoint


class AtspiRangeScroll(AccessibilityModel):
    op: Literal["range_scroll_to"] = "range_scroll_to"
    range: AccessibilityRangeReference
    alignment: AtspiScrollType


class AtspiRangeScrollPoint(AccessibilityModel):
    op: Literal["range_scroll_to_point"] = "range_scroll_to_point"
    range: AccessibilityRangeReference
    point: NativePoint


class AtspiSize(AccessibilityModel):
    op: Literal["component_size_set"] = "component_size_set"
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class AtspiExtents(AccessibilityModel):
    op: Literal["component_extents_set"] = "component_extents_set"
    point: NativePoint
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class AtspiTableSelection(AccessibilityModel):
    op: Literal["table_row_select", "table_row_unselect", "table_column_select", "table_column_unselect"]
    index: int = Field(ge=0)


type AtspiMutation = Annotated[
    AtspiAction
    | AtspiCommand
    | AtspiValue
    | AtspiChildSelection
    | AtspiTextContents
    | AtspiTextInsert
    | AtspiTextRange
    | AtspiTextPosition
    | AtspiSelectionSet
    | AtspiSelectionRemove
    | AtspiDocumentSelections
    | AtspiScroll
    | AtspiScrollPoint
    | AtspiRangeScroll
    | AtspiRangeScrollPoint
    | AtspiSize
    | AtspiExtents
    | AtspiTableSelection,
    Field(discriminator="op"),
]


class AXRequest(AccessibilityModel):
    backend: Literal["ax"] = "ax"
    request: AXMutation


class UIARequest(AccessibilityModel):
    backend: Literal["uia"] = "uia"
    request: UIAMutation


class AtspiRequest(AccessibilityModel):
    backend: Literal["atspi"] = "atspi"
    request: AtspiMutation


type AccessibilityMutation = Annotated[AXRequest | UIARequest | AtspiRequest, Field(discriminator="backend")]


@dataclass(frozen=True, slots=True)
class AccessibilityMutationResult:
    receipt: EnvironmentOperationReceipt
    effect: Literal["not_executed", "executed", "partial", "unknown"]
    native_status: Literal["returned_success", "returned_false", "error", "no_reply"]
    dispatch_complete: bool
    observation: AccessibilityNode | None = None
    # Native acknowledgement and optional node readback do not verify the user's task.
    application_outcome: Literal["unverified"] = "unverified"
