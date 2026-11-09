//! One closed UIA call per mutation, prepared and validated before dispatch.
use super::super::NativeMutation;
use super::*;
use crate::eip::{AccessibilityMutationResultNativeStatus as Status, ComputerEffect};
use windows::core::PCWSTR;

pub(super) const PATTERNS: &[(UIA_PATTERN_ID, &str, &[&str])] = &[
    (UIA_InvokePatternId, "InvokePattern", &["invoke"]),
    (UIA_ValuePatternId, "ValuePattern", &["value_set"]),
    (
        UIA_RangeValuePatternId,
        "RangeValuePattern",
        &["range_value_set"],
    ),
    (UIA_TogglePatternId, "TogglePattern", &["toggle"]),
    (
        UIA_ExpandCollapsePatternId,
        "ExpandCollapsePattern",
        &["expand", "collapse"],
    ),
    (
        UIA_SelectionItemPatternId,
        "SelectionItemPattern",
        &["selection_select", "selection_add", "selection_remove"],
    ),
    (
        UIA_ScrollPatternId,
        "ScrollPattern",
        &["scroll_amount", "scroll_percent"],
    ),
    (
        UIA_ScrollItemPatternId,
        "ScrollItemPattern",
        &["scroll_into_view"],
    ),
    (
        UIA_WindowPatternId,
        "WindowPattern",
        &["window_close", "window_state_set"],
    ),
    (
        UIA_TransformPatternId,
        "TransformPattern",
        &["transform_move", "transform_resize", "transform_rotate"],
    ),
    (
        UIA_TransformPattern2Id,
        "TransformPattern2",
        &["transform_zoom", "transform_zoom_unit"],
    ),
    (UIA_DockPatternId, "DockPattern", &["dock_set"]),
    (
        UIA_MultipleViewPatternId,
        "MultipleViewPattern",
        &["view_set"],
    ),
    (
        UIA_VirtualizedItemPatternId,
        "VirtualizedItemPattern",
        &["realize"],
    ),
    (
        UIA_LegacyIAccessiblePatternId,
        "LegacyIAccessiblePattern",
        &["legacy_default_action", "legacy_select", "legacy_value_set"],
    ),
    (
        UIA_TextPatternId,
        "TextPattern",
        &[
            "range_select",
            "range_add_selection",
            "range_remove_selection",
            "range_scroll_into_view",
        ],
    ),
];
impl Native {
    pub(super) fn mutate(
        &mut self,
        method: &str,
        p: &Value,
        b: &Budget,
        dispatch: &mut dyn FnMut() -> Result<(), EIPError>,
    ) -> Result<Value, EIPError> {
        let op = method
            .strip_prefix("computer.uia_")
            .ok_or_else(unsupported)?;
        let r = &p["request"];
        if r.get("op").is_some_and(|v| v != op) {
            return Err(invalid());
        }
        let node = self.node(&p["node"])?;
        // A stale COM object, disabled node or unknown protection never reaches
        // the side effect. No focus, realization or keyboard fallback is added.
        if !b.call(|| unsafe { node.CurrentIsEnabled() })?.as_bool() {
            return Err(error(ErrorType::InvalidParams, "UIA control is disabled"));
        }
        let protected = self.protected(&node, b)?;
        if protected
            && matches!(
                op,
                "value_set"
                    | "legacy_value_set"
                    | "range_value_set"
                    | "range_select"
                    | "range_add_selection"
                    | "range_remove_selection"
                    | "range_scroll_into_view"
                    | "range_show_context_menu"
            )
        {
            return Err(error(ErrorType::Denied, "protected UIA control"));
        }

        macro_rules! simple {
            ($ty:ty,$id:expr,$method:ident) => {{
                let pattern = self.pattern::<$ty>(&node, $id, b)?;
                Box::new(move || unsafe { pattern.$method() })
            }};
        }
        macro_rules! writable {
            ($pattern:expr) => {
                if b.call(|| unsafe { $pattern.CurrentIsReadOnly() })?
                    .as_bool()
                {
                    return Err(error(ErrorType::InvalidParams, "UIA value is read-only"));
                }
            };
        }
        let action: Box<dyn FnOnce() -> windows::core::Result<()>> = match op {
            "focus" => {
                if !b
                    .call(|| unsafe { node.CurrentIsKeyboardFocusable() })?
                    .as_bool()
                {
                    return Err(unsupported());
                }
                let n = node.clone();
                Box::new(move || unsafe { n.SetFocus() })
            }
            "invoke" => simple!(IUIAutomationInvokePattern, UIA_InvokePatternId, Invoke),
            "toggle" => simple!(IUIAutomationTogglePattern, UIA_TogglePatternId, Toggle),
            "expand" => simple!(
                IUIAutomationExpandCollapsePattern,
                UIA_ExpandCollapsePatternId,
                Expand
            ),
            "collapse" => simple!(
                IUIAutomationExpandCollapsePattern,
                UIA_ExpandCollapsePatternId,
                Collapse
            ),
            "selection_select" => simple!(
                IUIAutomationSelectionItemPattern,
                UIA_SelectionItemPatternId,
                Select
            ),
            "selection_add" => simple!(
                IUIAutomationSelectionItemPattern,
                UIA_SelectionItemPatternId,
                AddToSelection
            ),
            "selection_remove" => simple!(
                IUIAutomationSelectionItemPattern,
                UIA_SelectionItemPatternId,
                RemoveFromSelection
            ),
            "scroll_into_view" => simple!(
                IUIAutomationScrollItemPattern,
                UIA_ScrollItemPatternId,
                ScrollIntoView
            ),
            "realize" => simple!(
                IUIAutomationVirtualizedItemPattern,
                UIA_VirtualizedItemPatternId,
                Realize
            ),
            "window_close" => simple!(IUIAutomationWindowPattern, UIA_WindowPatternId, Close),
            "legacy_default_action" => simple!(
                IUIAutomationLegacyIAccessiblePattern,
                UIA_LegacyIAccessiblePatternId,
                DoDefaultAction
            ),
            "show_context_menu" => {
                let n = b.call(|| node.cast::<IUIAutomationElement3>())?;
                Box::new(move || unsafe { n.ShowContextMenu() })
            }
            "value_set" => {
                let pattern =
                    self.pattern::<IUIAutomationValuePattern>(&node, UIA_ValuePatternId, b)?;
                writable!(pattern);
                let value = BSTR::from(string(r, "value")?);
                Box::new(move || unsafe { pattern.SetValue(&value) })
            }
            "legacy_value_set" => {
                let pattern = self.pattern::<IUIAutomationLegacyIAccessiblePattern>(
                    &node,
                    UIA_LegacyIAccessiblePatternId,
                    b,
                )?;
                let state = b.call(|| unsafe { pattern.CurrentState() })?;
                if state & 0x40 != 0 {
                    return Err(error(
                        ErrorType::InvalidParams,
                        "legacy UIA value is read-only",
                    ));
                }
                let value = wide(string(r, "value")?);
                Box::new(move || unsafe { pattern.SetValue(PCWSTR(value.as_ptr())) })
            }
            "range_value_set" => {
                let pattern = self.pattern::<IUIAutomationRangeValuePattern>(
                    &node,
                    UIA_RangeValuePatternId,
                    b,
                )?;
                writable!(pattern);
                let value = finite(r, "value")?;
                let min = b.call(|| unsafe { pattern.CurrentMinimum() })?;
                let max = b.call(|| unsafe { pattern.CurrentMaximum() })?;
                if !min.is_finite() || !max.is_finite() || value < min || value > max {
                    return Err(invalid());
                }
                Box::new(move || unsafe { pattern.SetValue(value) })
            }
            "scroll_amount" => {
                let pattern =
                    self.pattern::<IUIAutomationScrollPattern>(&node, UIA_ScrollPatternId, b)?;
                let h = scroll_amount(r["horizontal"].as_str().unwrap_or("no_amount"))?;
                let v = scroll_amount(r["vertical"].as_str().unwrap_or("no_amount"))?;
                Box::new(move || unsafe { pattern.Scroll(h, v) })
            }
            "scroll_percent" => {
                let pattern =
                    self.pattern::<IUIAutomationScrollPattern>(&node, UIA_ScrollPatternId, b)?;
                let percent = |key| {
                    if r[key].is_null() {
                        Ok(-1.0)
                    } else {
                        let v = finite(r, key)?;
                        if !(0.0..=100.0).contains(&v) {
                            Err(invalid())
                        } else {
                            Ok(v)
                        }
                    }
                };
                let h = percent("horizontal")?;
                let v = percent("vertical")?;
                Box::new(move || unsafe { pattern.SetScrollPercent(h, v) })
            }
            "window_state_set" => {
                let pattern =
                    self.pattern::<IUIAutomationWindowPattern>(&node, UIA_WindowPatternId, b)?;
                let state = match string(r, "state")? {
                    "normal" => WindowVisualState_Normal,
                    "maximized" => {
                        if !b
                            .call(|| unsafe { pattern.CurrentCanMaximize() })?
                            .as_bool()
                        {
                            return Err(unsupported());
                        }
                        WindowVisualState_Maximized
                    }
                    "minimized" => {
                        if !b
                            .call(|| unsafe { pattern.CurrentCanMinimize() })?
                            .as_bool()
                        {
                            return Err(unsupported());
                        }
                        WindowVisualState_Minimized
                    }
                    _ => return Err(invalid()),
                };
                Box::new(move || unsafe { pattern.SetWindowVisualState(state) })
            }
            "transform_move" => {
                let pattern = self.pattern::<IUIAutomationTransformPattern>(
                    &node,
                    UIA_TransformPatternId,
                    b,
                )?;
                if !b.call(|| unsafe { pattern.CurrentCanMove() })?.as_bool() {
                    return Err(unsupported());
                }
                let x = finite(r, "x")?;
                let y = finite(r, "y")?;
                Box::new(move || unsafe { pattern.Move(x, y) })
            }
            "transform_resize" => {
                let pattern = self.pattern::<IUIAutomationTransformPattern>(
                    &node,
                    UIA_TransformPatternId,
                    b,
                )?;
                if !b.call(|| unsafe { pattern.CurrentCanResize() })?.as_bool() {
                    return Err(unsupported());
                }
                let width = positive(r, "width")?;
                let height = positive(r, "height")?;
                Box::new(move || unsafe { pattern.Resize(width, height) })
            }
            "transform_rotate" => {
                let pattern = self.pattern::<IUIAutomationTransformPattern>(
                    &node,
                    UIA_TransformPatternId,
                    b,
                )?;
                if !b.call(|| unsafe { pattern.CurrentCanRotate() })?.as_bool() {
                    return Err(unsupported());
                }
                let degrees = finite(r, "degrees")?;
                Box::new(move || unsafe { pattern.Rotate(degrees) })
            }
            "transform_zoom" | "transform_zoom_unit" => {
                let pattern = self.pattern::<IUIAutomationTransformPattern2>(
                    &node,
                    UIA_TransformPattern2Id,
                    b,
                )?;
                if !b.call(|| unsafe { pattern.CurrentCanZoom() })?.as_bool() {
                    return Err(unsupported());
                }
                if op == "transform_zoom" {
                    let value = positive(r, "percent")?;
                    let min = b.call(|| unsafe { pattern.CurrentZoomMinimum() })?;
                    let max = b.call(|| unsafe { pattern.CurrentZoomMaximum() })?;
                    if value < min || value > max {
                        return Err(invalid());
                    }
                    Box::new(move || unsafe { pattern.Zoom(value) })
                } else {
                    let unit = zoom_unit(string(r, "unit")?)?;
                    Box::new(move || unsafe { pattern.ZoomByUnit(unit) })
                }
            }
            "dock_set" => {
                let pattern =
                    self.pattern::<IUIAutomationDockPattern>(&node, UIA_DockPatternId, b)?;
                let position = match string(r, "position")? {
                    "top" => DockPosition_Top,
                    "left" => DockPosition_Left,
                    "bottom" => DockPosition_Bottom,
                    "right" => DockPosition_Right,
                    "fill" => DockPosition_Fill,
                    "none" => DockPosition_None,
                    _ => return Err(invalid()),
                };
                Box::new(move || unsafe { pattern.SetDockPosition(position) })
            }
            "view_set" => {
                let pattern = self.pattern::<IUIAutomationMultipleViewPattern>(
                    &node,
                    UIA_MultipleViewPatternId,
                    b,
                )?;
                let id = r["view_id"]
                    .as_i64()
                    .and_then(|v| i32::try_from(v).ok())
                    .ok_or_else(invalid)?;
                let ids = OwnedArray(b.call(|| unsafe { pattern.GetCurrentSupportedViews() })?);
                if !ids.integers(b)?.contains(&id) {
                    return Err(invalid());
                }
                Box::new(move || unsafe { pattern.SetCurrentView(id) })
            }
            "legacy_select" => {
                let pattern = self.pattern::<IUIAutomationLegacyIAccessiblePattern>(
                    &node,
                    UIA_LegacyIAccessiblePatternId,
                    b,
                )?;
                let mut flags = match string(r, "selection")? {
                    "none" => 0,
                    "take" => 2,
                    "extend" => 4,
                    "add" => 8,
                    "remove" => 16,
                    _ => return Err(invalid()),
                };
                if r["take_focus"].as_bool().unwrap_or(false) {
                    flags |= 1;
                }
                Box::new(move || unsafe { pattern.Select(flags) })
            }
            "range_select"
            | "range_add_selection"
            | "range_remove_selection"
            | "range_scroll_into_view"
            | "range_show_context_menu" => {
                let range = self.range_operand(&r["range"], &p["node"])?;
                if self.operation_support(&node, op, b)?.is_none() {
                    return Err(unsupported());
                }
                match op {
                    "range_select" => Box::new(move || unsafe { range.Select() }),
                    "range_add_selection" => Box::new(move || unsafe { range.AddToSelection() }),
                    "range_remove_selection" => {
                        Box::new(move || unsafe { range.RemoveFromSelection() })
                    }
                    "range_scroll_into_view" => {
                        let top = r["align_top"].as_bool().unwrap_or(true);
                        Box::new(move || unsafe { range.ScrollIntoView(top) })
                    }
                    _ => {
                        let range = b.call(|| range.cast::<IUIAutomationTextRange2>())?;
                        Box::new(move || unsafe { range.ShowContextMenu() })
                    }
                }
            }
            _ => return Err(unsupported()),
        };
        b.check()?;
        crate::computer::windows::ready()?;
        dispatch()?;
        // No budget/readback error may erase a returned native acknowledgement.
        let status = match action() {
            Ok(()) => Status::ReturnedSuccess,
            Err(_) => Status::Error,
        };
        let observation = if p["readback"].as_bool().unwrap_or(false) {
            self.sample(&node, b)
                .ok()
                .and_then(|v| serde_json::from_value(v).ok())
        } else {
            None
        };
        serde_json::to_value(NativeMutation {
            effect: if status == Status::ReturnedSuccess {
                ComputerEffect::Executed
            } else {
                ComputerEffect::Unknown
            },
            native_status: status,
            dispatch_complete: true,
            observation,
        })
        .map_err(|_| {
            error(
                ErrorType::InternalError,
                "cannot encode UIA native evidence",
            )
        })
    }
}
fn finite(v: &Value, key: &str) -> Result<f64, EIPError> {
    v[key]
        .as_f64()
        .filter(|v| v.is_finite())
        .ok_or_else(invalid)
}
fn positive(v: &Value, key: &str) -> Result<f64, EIPError> {
    finite(v, key).and_then(|v| if v > 0.0 { Ok(v) } else { Err(invalid()) })
}
pub(super) fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}
fn scroll_amount(s: &str) -> Result<ScrollAmount, EIPError> {
    Ok(match s {
        "large_decrement" => ScrollAmount_LargeDecrement,
        "small_decrement" => ScrollAmount_SmallDecrement,
        "no_amount" => ScrollAmount_NoAmount,
        "large_increment" => ScrollAmount_LargeIncrement,
        "small_increment" => ScrollAmount_SmallIncrement,
        _ => return Err(invalid()),
    })
}
fn zoom_unit(s: &str) -> Result<ZoomUnit, EIPError> {
    Ok(match s {
        "no_amount" => ZoomUnit_NoAmount,
        "large_decrement" => ZoomUnit_LargeDecrement,
        "small_decrement" => ZoomUnit_SmallDecrement,
        "large_increment" => ZoomUnit_LargeIncrement,
        "small_increment" => ZoomUnit_SmallIncrement,
        _ => return Err(invalid()),
    })
}

// Pattern presence alone does not establish writability or transform support.
// Operand-dependent native constraints remain candidates and are preflighted.
impl Native {
    pub(super) fn operation_support(
        &self,
        n: &IUIAutomationElement,
        op: &str,
        b: &Budget,
    ) -> Result<Option<&'static str>, EIPError> {
        let flag = match op {
            "value_set" => Some((UIA_ValueIsReadOnlyPropertyId, false)),
            "range_value_set" => Some((UIA_RangeValueIsReadOnlyPropertyId, false)),
            "transform_move" => Some((UIA_TransformCanMovePropertyId, true)),
            "transform_resize" => Some((UIA_TransformCanResizePropertyId, true)),
            "transform_rotate" => Some((UIA_TransformCanRotatePropertyId, true)),
            "transform_zoom" | "transform_zoom_unit" => {
                Some((UIA_Transform2CanZoomPropertyId, true))
            }
            _ => None,
        };
        if let Some((id, expected)) = flag {
            let raw =
                read::OwnedVariant(b.call(|| unsafe { n.GetCurrentPropertyValueEx(id, true) })?);
            let v = unsafe { &raw.Anonymous.Anonymous };
            if v.vt != VT_BOOL {
                return Ok(Some("candidate"));
            }
            let actual = unsafe { v.Anonymous.boolVal.0 != 0 };
            return Ok((actual == expected).then_some("yes"));
        }
        if op == "legacy_value_set" {
            let p = self.pattern::<IUIAutomationLegacyIAccessiblePattern>(
                n,
                UIA_LegacyIAccessiblePatternId,
                b,
            )?;
            return Ok((b.call(|| unsafe { p.CurrentState() })? & 0x40 == 0).then_some("yes"));
        }
        if matches!(
            op,
            "range_select" | "range_add_selection" | "range_remove_selection"
        ) {
            let p = self.pattern::<IUIAutomationTextPattern>(n, UIA_TextPatternId, b)?;
            let selection = b.call(|| unsafe { p.SupportedTextSelection() })?;
            if !selection_supported(op, selection) {
                return Ok(None);
            }
        }
        Ok(Some(
            if matches!(
                op,
                "scroll_amount"
                    | "scroll_percent"
                    | "window_state_set"
                    | "view_set"
                    | "legacy_select"
            ) {
                "candidate"
            } else {
                "yes"
            },
        ))
    }
}
pub(super) fn content_mutation(op: &str) -> bool {
    matches!(
        op,
        "value_set"
            | "legacy_value_set"
            | "range_value_set"
            | "range_select"
            | "range_add_selection"
            | "range_remove_selection"
            | "range_scroll_into_view"
            | "range_show_context_menu"
    )
}

pub(super) fn selection_supported(op: &str, selection: SupportedTextSelection) -> bool {
    selection != SupportedTextSelection_None
        && (op != "range_add_selection" || selection == SupportedTextSelection_Multiple)
}
