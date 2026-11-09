"""Translate Session-local accessibility objects into execution-bound observations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip
from pydantic import BaseModel, JsonValue, ValidationError

from a13n_harness.environment.accessibility import (
    AccessibilityContent,
    AccessibilityCursor,
    AccessibilityDescription,
    AccessibilityFind,
    AccessibilityLimits,
    AccessibilityNode,
    AccessibilityNodeReference,
    AccessibilityRangeQuery,
    AccessibilityRangeReadRequest,
    AccessibilityRangeReference,
    AccessibilityRangeResult,
    AccessibilityRead,
    AccessibilityRootScope,
    AccessibilityTree,
)
from a13n_harness.environment.accessibility_mutations import AccessibilityMutation, AccessibilityMutationResult
from a13n_harness.environment.computer import ComputerObservation, ComputerPoint
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.eip._common import convert_receipt, invoke, new_context, session_client

type MutationDispatch = Callable[[eip.EIPClient, dict[str, JsonValue]], Awaitable[eip.AccessibilityMutationResult]]


def _mutation[P: BaseModel](
    params_type: type[P],
    call: Callable[[eip.EIPClient, P], Awaitable[eip.AccessibilityMutationResult]],
) -> MutationDispatch:
    async def dispatch(client: eip.EIPClient, arguments: dict[str, JsonValue]) -> eip.AccessibilityMutationResult:
        try:
            params = params_type.model_validate(arguments)
        except ValidationError as error:
            raise EnvironmentError(
                "Accessibility arguments violate the EIP contract",
                code="environment_request_invalid",
                details={"dispatch_stage": "pre_dispatch"},
            ) from error
        return await call(client, params)

    return dispatch


# Exact generated entrypoints, never an arbitrary native method name.
_MUTATIONS: dict[str, MutationDispatch] = {
    "computer.ax_disclosing_set": _mutation(
        eip.ComputerAXDisclosingSetParams, eip.EIPClient.computer_ax_disclosing_set
    ),
    "computer.ax_expanded_set": _mutation(eip.ComputerAXExpandedSetParams, eip.EIPClient.computer_ax_expanded_set),
    "computer.ax_focus": _mutation(eip.ComputerAXFocusParams, eip.EIPClient.computer_ax_focus),
    "computer.ax_focused_window_set": _mutation(
        eip.ComputerAXFocusedWindowSetParams, eip.EIPClient.computer_ax_focused_window_set
    ),
    "computer.ax_main_set": _mutation(eip.ComputerAXMainSetParams, eip.EIPClient.computer_ax_main_set),
    "computer.ax_minimized_set": _mutation(eip.ComputerAXMinimizedSetParams, eip.EIPClient.computer_ax_minimized_set),
    "computer.ax_perform_action": _mutation(
        eip.ComputerAXPerformActionParams, eip.EIPClient.computer_ax_perform_action
    ),
    "computer.ax_position_set": _mutation(eip.ComputerAXPositionSetParams, eip.EIPClient.computer_ax_position_set),
    "computer.ax_selected_children_set": _mutation(
        eip.ComputerAXSelectedChildrenSetParams, eip.EIPClient.computer_ax_selected_children_set
    ),
    "computer.ax_selected_columns_set": _mutation(
        eip.ComputerAXSelectedColumnsSetParams, eip.EIPClient.computer_ax_selected_columns_set
    ),
    "computer.ax_selected_rows_set": _mutation(
        eip.ComputerAXSelectedRowsSetParams, eip.EIPClient.computer_ax_selected_rows_set
    ),
    "computer.ax_selected_set": _mutation(eip.ComputerAXSelectedSetParams, eip.EIPClient.computer_ax_selected_set),
    "computer.ax_selected_text_set": _mutation(
        eip.ComputerAXSelectedTextSetParams, eip.EIPClient.computer_ax_selected_text_set
    ),
    "computer.ax_size_set": _mutation(eip.ComputerAXSizeSetParams, eip.EIPClient.computer_ax_size_set),
    "computer.ax_text_range_select": _mutation(
        eip.ComputerAXTextRangeSelectParams, eip.EIPClient.computer_ax_text_range_select
    ),
    "computer.ax_text_ranges_select": _mutation(
        eip.ComputerAXTextRangesSelectParams, eip.EIPClient.computer_ax_text_ranges_select
    ),
    "computer.ax_value_number_set": _mutation(
        eip.ComputerAXValueNumberSetParams, eip.EIPClient.computer_ax_value_number_set
    ),
    "computer.ax_value_string_set": _mutation(
        eip.ComputerAXValueStringSetParams, eip.EIPClient.computer_ax_value_string_set
    ),
    "computer.ax_visible_text_range_set": _mutation(
        eip.ComputerAXVisibleTextRangeSetParams, eip.EIPClient.computer_ax_visible_text_range_set
    ),
    "computer.uia_collapse": _mutation(eip.ComputerUIACollapseParams, eip.EIPClient.computer_uia_collapse),
    "computer.uia_dock_set": _mutation(eip.ComputerUIADockSetParams, eip.EIPClient.computer_uia_dock_set),
    "computer.uia_expand": _mutation(eip.ComputerUIAExpandParams, eip.EIPClient.computer_uia_expand),
    "computer.uia_focus": _mutation(eip.ComputerUIAFocusParams, eip.EIPClient.computer_uia_focus),
    "computer.uia_invoke": _mutation(eip.ComputerUIAInvokeParams, eip.EIPClient.computer_uia_invoke),
    "computer.uia_legacy_default_action": _mutation(
        eip.ComputerUIALegacyDefaultActionParams, eip.EIPClient.computer_uia_legacy_default_action
    ),
    "computer.uia_legacy_select": _mutation(
        eip.ComputerUIALegacySelectParams, eip.EIPClient.computer_uia_legacy_select
    ),
    "computer.uia_legacy_value_set": _mutation(
        eip.ComputerUIALegacyValueSetParams, eip.EIPClient.computer_uia_legacy_value_set
    ),
    "computer.uia_range_add_selection": _mutation(
        eip.ComputerUIARangeAddSelectionParams, eip.EIPClient.computer_uia_range_add_selection
    ),
    "computer.uia_range_remove_selection": _mutation(
        eip.ComputerUIARangeRemoveSelectionParams, eip.EIPClient.computer_uia_range_remove_selection
    ),
    "computer.uia_range_scroll_into_view": _mutation(
        eip.ComputerUIARangeScrollIntoViewParams, eip.EIPClient.computer_uia_range_scroll_into_view
    ),
    "computer.uia_range_select": _mutation(eip.ComputerUIARangeSelectParams, eip.EIPClient.computer_uia_range_select),
    "computer.uia_range_show_context_menu": _mutation(
        eip.ComputerUIARangeShowContextMenuParams, eip.EIPClient.computer_uia_range_show_context_menu
    ),
    "computer.uia_range_value_set": _mutation(
        eip.ComputerUIARangeValueSetParams, eip.EIPClient.computer_uia_range_value_set
    ),
    "computer.uia_realize": _mutation(eip.ComputerUIARealizeParams, eip.EIPClient.computer_uia_realize),
    "computer.uia_scroll_amount": _mutation(
        eip.ComputerUIAScrollAmountParams, eip.EIPClient.computer_uia_scroll_amount
    ),
    "computer.uia_scroll_into_view": _mutation(
        eip.ComputerUIAScrollIntoViewParams, eip.EIPClient.computer_uia_scroll_into_view
    ),
    "computer.uia_scroll_percent": _mutation(
        eip.ComputerUIAScrollPercentParams, eip.EIPClient.computer_uia_scroll_percent
    ),
    "computer.uia_selection_add": _mutation(
        eip.ComputerUIASelectionAddParams, eip.EIPClient.computer_uia_selection_add
    ),
    "computer.uia_selection_remove": _mutation(
        eip.ComputerUIASelectionRemoveParams, eip.EIPClient.computer_uia_selection_remove
    ),
    "computer.uia_selection_select": _mutation(
        eip.ComputerUIASelectionSelectParams, eip.EIPClient.computer_uia_selection_select
    ),
    "computer.uia_show_context_menu": _mutation(
        eip.ComputerUIAShowContextMenuParams, eip.EIPClient.computer_uia_show_context_menu
    ),
    "computer.uia_toggle": _mutation(eip.ComputerUIAToggleParams, eip.EIPClient.computer_uia_toggle),
    "computer.uia_transform_move": _mutation(
        eip.ComputerUIATransformMoveParams, eip.EIPClient.computer_uia_transform_move
    ),
    "computer.uia_transform_resize": _mutation(
        eip.ComputerUIATransformResizeParams, eip.EIPClient.computer_uia_transform_resize
    ),
    "computer.uia_transform_rotate": _mutation(
        eip.ComputerUIATransformRotateParams, eip.EIPClient.computer_uia_transform_rotate
    ),
    "computer.uia_transform_zoom": _mutation(
        eip.ComputerUIATransformZoomParams, eip.EIPClient.computer_uia_transform_zoom
    ),
    "computer.uia_transform_zoom_unit": _mutation(
        eip.ComputerUIATransformZoomUnitParams, eip.EIPClient.computer_uia_transform_zoom_unit
    ),
    "computer.uia_value_set": _mutation(eip.ComputerUIAValueSetParams, eip.EIPClient.computer_uia_value_set),
    "computer.uia_view_set": _mutation(eip.ComputerUIAViewSetParams, eip.EIPClient.computer_uia_view_set),
    "computer.uia_window_close": _mutation(eip.ComputerUIAWindowCloseParams, eip.EIPClient.computer_uia_window_close),
    "computer.uia_window_state_set": _mutation(
        eip.ComputerUIAWindowStateSetParams, eip.EIPClient.computer_uia_window_state_set
    ),
    "computer.atspi_caret_set": _mutation(eip.ComputerAtspiCaretSetParams, eip.EIPClient.computer_atspi_caret_set),
    "computer.atspi_component_extents_set": _mutation(
        eip.ComputerAtspiComponentExtentsSetParams, eip.EIPClient.computer_atspi_component_extents_set
    ),
    "computer.atspi_component_position_set": _mutation(
        eip.ComputerAtspiComponentPositionSetParams, eip.EIPClient.computer_atspi_component_position_set
    ),
    "computer.atspi_component_size_set": _mutation(
        eip.ComputerAtspiComponentSizeSetParams, eip.EIPClient.computer_atspi_component_size_set
    ),
    "computer.atspi_do_action": _mutation(eip.ComputerAtspiDoActionParams, eip.EIPClient.computer_atspi_do_action),
    "computer.atspi_document_selections_set": _mutation(
        eip.ComputerAtspiDocumentSelectionsSetParams, eip.EIPClient.computer_atspi_document_selections_set
    ),
    "computer.atspi_focus": _mutation(eip.ComputerAtspiFocusParams, eip.EIPClient.computer_atspi_focus),
    "computer.atspi_range_scroll_to": _mutation(
        eip.ComputerAtspiRangeScrollToParams, eip.EIPClient.computer_atspi_range_scroll_to
    ),
    "computer.atspi_range_scroll_to_point": _mutation(
        eip.ComputerAtspiRangeScrollToPointParams, eip.EIPClient.computer_atspi_range_scroll_to_point
    ),
    "computer.atspi_scroll_to": _mutation(eip.ComputerAtspiScrollToParams, eip.EIPClient.computer_atspi_scroll_to),
    "computer.atspi_scroll_to_point": _mutation(
        eip.ComputerAtspiScrollToPointParams, eip.EIPClient.computer_atspi_scroll_to_point
    ),
    "computer.atspi_selection_add_child": _mutation(
        eip.ComputerAtspiSelectionAddChildParams, eip.EIPClient.computer_atspi_selection_add_child
    ),
    "computer.atspi_selection_all": _mutation(
        eip.ComputerAtspiSelectionAllParams, eip.EIPClient.computer_atspi_selection_all
    ),
    "computer.atspi_selection_clear": _mutation(
        eip.ComputerAtspiSelectionClearParams, eip.EIPClient.computer_atspi_selection_clear
    ),
    "computer.atspi_selection_remove_child": _mutation(
        eip.ComputerAtspiSelectionRemoveChildParams, eip.EIPClient.computer_atspi_selection_remove_child
    ),
    "computer.atspi_selection_remove_selected": _mutation(
        eip.ComputerAtspiSelectionRemoveSelectedParams, eip.EIPClient.computer_atspi_selection_remove_selected
    ),
    "computer.atspi_table_column_select": _mutation(
        eip.ComputerAtspiTableColumnSelectParams, eip.EIPClient.computer_atspi_table_column_select
    ),
    "computer.atspi_table_column_unselect": _mutation(
        eip.ComputerAtspiTableColumnUnselectParams, eip.EIPClient.computer_atspi_table_column_unselect
    ),
    "computer.atspi_table_row_select": _mutation(
        eip.ComputerAtspiTableRowSelectParams, eip.EIPClient.computer_atspi_table_row_select
    ),
    "computer.atspi_table_row_unselect": _mutation(
        eip.ComputerAtspiTableRowUnselectParams, eip.EIPClient.computer_atspi_table_row_unselect
    ),
    "computer.atspi_text_contents_set": _mutation(
        eip.ComputerAtspiTextContentsSetParams, eip.EIPClient.computer_atspi_text_contents_set
    ),
    "computer.atspi_text_copy": _mutation(eip.ComputerAtspiTextCopyParams, eip.EIPClient.computer_atspi_text_copy),
    "computer.atspi_text_cut": _mutation(eip.ComputerAtspiTextCutParams, eip.EIPClient.computer_atspi_text_cut),
    "computer.atspi_text_delete": _mutation(
        eip.ComputerAtspiTextDeleteParams, eip.EIPClient.computer_atspi_text_delete
    ),
    "computer.atspi_text_insert": _mutation(
        eip.ComputerAtspiTextInsertParams, eip.EIPClient.computer_atspi_text_insert
    ),
    "computer.atspi_text_paste": _mutation(eip.ComputerAtspiTextPasteParams, eip.EIPClient.computer_atspi_text_paste),
    "computer.atspi_text_selection_add": _mutation(
        eip.ComputerAtspiTextSelectionAddParams, eip.EIPClient.computer_atspi_text_selection_add
    ),
    "computer.atspi_text_selection_remove": _mutation(
        eip.ComputerAtspiTextSelectionRemoveParams, eip.EIPClient.computer_atspi_text_selection_remove
    ),
    "computer.atspi_text_selection_set": _mutation(
        eip.ComputerAtspiTextSelectionSetParams, eip.EIPClient.computer_atspi_text_selection_set
    ),
    "computer.atspi_value_set": _mutation(eip.ComputerAtspiValueSetParams, eip.EIPClient.computer_atspi_value_set),
}


class EIPAccessibilityOperations:
    def __init__(self, session: EIPSession, *, execution_id: str, generation: str) -> None:
        self._session = session
        self._execution_id = execution_id
        self._generation = generation

    def _wire(self, value: BaseModel) -> dict[str, JsonValue]:
        def strip(item: JsonValue) -> JsonValue:
            if isinstance(item, list):
                return [strip(child) for child in item]
            if not isinstance(item, dict):
                return item
            if {"execution_id", "observed_generation", "node_handle", "backend_epoch"} <= item.keys():
                if (item["execution_id"], item["observed_generation"]) != (self._execution_id, self._generation):
                    raise EnvironmentError(
                        "Accessibility reference belongs to another execution",
                        code="environment_stale_mount",
                        details={"dispatch_stage": "pre_dispatch"},
                    )
                return {
                    key: strip(child)
                    for key, child in item.items()
                    if key not in {"execution_id", "observed_generation"}
                }
            return {key: strip(child) for key, child in item.items()}

        result = strip(value.model_dump(mode="json", exclude_none=True))
        assert isinstance(result, dict)
        return result

    def _bind(self, value: JsonValue) -> JsonValue:
        if isinstance(value, list):
            return [self._bind(child) for child in value]
        if not isinstance(value, dict):
            return value
        result = {key: self._bind(child) for key, child in value.items()}
        if set(value) == {"backend", "backend_epoch", "node_handle"}:
            result.update(execution_id=self._execution_id, observed_generation=self._generation)
        return result

    async def _read[T: BaseModel](self, call: Awaitable[BaseModel], model: type[T]) -> T:
        result = await invoke(call)
        try:
            return model.model_validate(self._bind(result.model_dump(mode="json", exclude_none=True)))
        except ValidationError as error:
            raise EnvironmentError(
                "EIP accessibility observation violates its contract", code="environment_provider_failure"
            ) from error

    async def describe(self) -> AccessibilityDescription:
        return await self._read(
            session_client(self._session).computer_a11y_describe(eip.ComputerA11yDescribeParams(context=new_context())),
            AccessibilityDescription,
        )

    async def roots(self, scope: AccessibilityRootScope, *, limits: AccessibilityLimits) -> AccessibilityTree:
        params = eip.ComputerA11yRootsParams.model_validate(
            {
                "context": new_context(timeout_seconds=limits.timeout_ms / 1000),
                "scope": self._wire(scope),
                "limits": self._wire(limits),
            }
        )
        return await self._read(session_client(self._session).computer_a11y_roots(params), AccessibilityTree)

    async def roots_at_point(
        self, observation: ComputerObservation, point: ComputerPoint, *, limits: AccessibilityLimits
    ) -> AccessibilityTree:
        params = eip.ComputerA11yRootsParams.model_validate(
            {
                "context": new_context(timeout_seconds=limits.timeout_ms / 1000),
                "scope": {
                    "kind": "screenshot_point",
                    "observation_id": observation.observation_id,
                    "image_point": point.model_dump(),
                },
                "limits": self._wire(limits),
            }
        )
        return await self._read(session_client(self._session).computer_a11y_roots(params), AccessibilityTree)

    async def inspect(
        self,
        node: AccessibilityNodeReference,
        *,
        limits: AccessibilityLimits,
        view: Literal["control", "content", "raw"] = "control",
        cursor: AccessibilityCursor | None = None,
        projection: ComputerObservation | None = None,
    ) -> AccessibilityTree:
        params = eip.ComputerA11yInspectParams.model_validate(
            {
                "context": new_context(timeout_seconds=limits.timeout_ms / 1000),
                "node": self._wire(node),
                "limits": self._wire(limits),
                "view": view,
                **({"cursor": self._wire(cursor)} if cursor is not None else {}),
                **({"projection_observation_id": projection.observation_id} if projection is not None else {}),
            }
        )
        return await self._read(session_client(self._session).computer_a11y_inspect(params), AccessibilityTree)

    async def find(
        self,
        node: AccessibilityNodeReference,
        query: AccessibilityFind,
        *,
        limits: AccessibilityLimits,
        view: Literal["control", "content", "raw"] = "control",
        cursor: AccessibilityCursor | None = None,
    ) -> AccessibilityTree:
        params = eip.ComputerA11yFindParams.model_validate(
            {
                "context": new_context(timeout_seconds=limits.timeout_ms / 1000),
                "node": self._wire(node),
                "query": self._wire(query),
                "limits": self._wire(limits),
                "view": view,
                **({"cursor": self._wire(cursor)} if cursor is not None else {}),
            }
        )
        return await self._read(session_client(self._session).computer_a11y_find(params), AccessibilityTree)

    async def read(
        self,
        node: AccessibilityNodeReference,
        request: AccessibilityRead,
        *,
        limits: AccessibilityLimits,
    ) -> AccessibilityContent:
        params = eip.ComputerA11yReadParams.model_validate(
            {
                "context": new_context(timeout_seconds=limits.timeout_ms / 1000),
                "node": self._wire(node),
                "request": self._wire(request),
                "limits": self._wire(limits),
            }
        )
        return await self._read(session_client(self._session).computer_a11y_read(params), AccessibilityContent)

    async def range(
        self,
        node: AccessibilityNodeReference,
        query: AccessibilityRangeQuery,
        *,
        limits: AccessibilityLimits,
        source: AccessibilityRangeReference | None = None,
    ) -> AccessibilityRangeResult:
        params = eip.ComputerA11yRangeParams.model_validate(
            {
                "context": new_context(timeout_seconds=limits.timeout_ms / 1000),
                "node": self._wire(node),
                "query": self._wire(query),
                "limits": self._wire(limits),
                **({"source": self._wire(source)} if source is not None else {}),
            }
        )
        return await self._read(session_client(self._session).computer_a11y_range(params), AccessibilityRangeResult)

    async def range_read(
        self,
        reference: AccessibilityRangeReference,
        request: AccessibilityRangeReadRequest,
        *,
        limits: AccessibilityLimits,
    ) -> AccessibilityRangeResult:
        params = eip.ComputerA11yRangeReadParams.model_validate(
            {
                "context": new_context(timeout_seconds=limits.timeout_ms / 1000),
                "reference": self._wire(reference),
                "request": self._wire(request),
                "limits": self._wire(limits),
            }
        )
        return await self._read(
            session_client(self._session).computer_a11y_range_read(params), AccessibilityRangeResult
        )

    async def execute(
        self,
        node: AccessibilityNodeReference,
        request: AccessibilityMutation,
        *,
        readback: bool = False,
    ) -> AccessibilityMutationResult:
        method = f"computer.{request.backend}_{request.request.op}"
        if request.backend != node.backend:
            raise EnvironmentError(
                "Accessibility backend mismatch",
                code="environment_request_invalid",
                details={"dispatch_stage": "pre_dispatch"},
            )
        context = new_context(timeout_seconds=15)
        arguments: dict[str, JsonValue] = {
            "context": context.model_dump(mode="json", exclude_none=True),
            "node": self._wire(node),
            "request": self._wire(request.request),
            "readback": readback,
        }
        try:
            result = await invoke(_MUTATIONS[method](session_client(self._session), arguments))
            if (result.receipt.method, result.receipt.operation_id) != (method, context.operation_id):
                raise EnvironmentError(
                    "Accessibility mutation evidence does not match its request",
                    code="environment_unknown_outcome",
                    retry_hint="reconcile_first",
                )
            receipt = convert_receipt(
                result.receipt, session=self._session, execution_id=self._execution_id, generation=self._generation
            )
            observation = (
                None
                if result.observation is None
                else AccessibilityNode.model_validate(
                    self._bind(result.observation.model_dump(mode="json", exclude_none=True))
                )
            )
            return AccessibilityMutationResult(
                receipt=receipt,
                effect=result.effect.value,
                native_status=result.native_status.value,
                dispatch_complete=result.dispatch_complete,
                observation=observation,
            )
        except EnvironmentError as error:
            if error.retry_hint == "new_run" and "dispatch_stage" not in error.details:
                raise EnvironmentError(
                    "Accessibility mutation outcome is unconfirmed",
                    code=error.code,
                    retry_hint="reconcile_first",
                    details={
                        **error.details,
                        "dispatch_stage": "unknown",
                        "hint": "Reconnect if needed and inspect current UI state before another action; never replay automatically.",
                    },
                ) from error
            raise
        except ValidationError as error:
            raise EnvironmentError(
                "Accessibility mutation returned invalid evidence",
                code="environment_unknown_outcome",
                retry_hint="reconcile_first",
                details={"dispatch_stage": "unknown"},
            ) from error
