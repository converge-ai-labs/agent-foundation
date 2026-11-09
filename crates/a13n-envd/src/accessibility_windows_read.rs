//! Bounded UIA content and pattern observations. Property IDs are an explicit
//! read-only allowlist; the public request cannot invoke arbitrary COM members.
use super::*;
use std::{mem::ManuallyDrop, ops::Deref};

impl Native {
    pub(super) fn read(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let owner = &p["node"];
        let n = self.node(owner)?;
        let r = &p["request"];
        let mut result = json!({"node":owner,"sampled_at":now(),"completeness":{"complete":true},"nodes":[],"properties":[],"ranges":[],"field_errors":[]});
        let kind = string(r, "kind")?;
        if self.protected(&n, b)? && !matches!(kind, "navigate" | "wait_for_input_idle") {
            result["text"] = json!({"status":"protected","source":"UIA"});
            return Ok(result);
        }
        let outcome = (|| {
            match kind {
                "text" | "document" => {
                    if !r["span"].is_null() {
                        return Err(unsupported());
                    }
                    match self.pattern::<IUIAutomationTextPattern>(&n, UIA_TextPatternId, b) {
                        Ok(pattern) => {
                            let range = b.call(|| unsafe { pattern.DocumentRange() })?;
                            result["text"] = self.range_text(&range, b)?;
                        }
                        Err(e) if e.data.error_type == ErrorType::Unsupported => {
                            let pattern = self.pattern::<IUIAutomationValuePattern>(
                                &n,
                                UIA_ValuePatternId,
                                b,
                            )?;
                            let value = b.call(|| unsafe { pattern.CurrentValue() })?;
                            let text = b.text(value.to_string());
                            result["text"] = json!({"status":if text.is_empty(){"empty"}else{"text"},"source":"UIA.ValuePattern","text":text});
                        }
                        Err(e) => return Err(e),
                    }
                }
                "numeric_value" => {
                    self.properties(
                        &n,
                        &mut result,
                        &[
                            UIA_RangeValueValuePropertyId,
                            UIA_RangeValueMinimumPropertyId,
                            UIA_RangeValueMaximumPropertyId,
                            UIA_RangeValueSmallChangePropertyId,
                            UIA_RangeValueLargeChangePropertyId,
                            UIA_RangeValueIsReadOnlyPropertyId,
                        ],
                        b,
                    )?;
                }
                "scroll" => self.properties(
                    &n,
                    &mut result,
                    &[
                        UIA_ScrollHorizontallyScrollablePropertyId,
                        UIA_ScrollHorizontalScrollPercentPropertyId,
                        UIA_ScrollHorizontalViewSizePropertyId,
                        UIA_ScrollVerticallyScrollablePropertyId,
                        UIA_ScrollVerticalScrollPercentPropertyId,
                        UIA_ScrollVerticalViewSizePropertyId,
                    ],
                    b,
                )?,
                "selection" => {
                    let pattern = self.pattern::<IUIAutomationSelectionPattern>(
                        &n,
                        UIA_SelectionPatternId,
                        b,
                    )?;
                    let array = b.call(|| unsafe { pattern.GetCurrentSelection() })?;
                    self.read_elements(&array, &mut result, b)?;
                    self.properties(
                        &n,
                        &mut result,
                        &[
                            UIA_SelectionCanSelectMultiplePropertyId,
                            UIA_SelectionIsSelectionRequiredPropertyId,
                        ],
                        b,
                    )?;
                    if let Ok(pattern) = self.pattern::<IUIAutomationSelectionPattern2>(
                        &n,
                        UIA_SelectionPattern2Id,
                        b,
                    ) {
                        property(
                            &mut result,
                            "item_count",
                            "SelectionPattern2",
                            json!(b.call(|| unsafe { pattern.CurrentItemCount() })?),
                        );
                    }
                }
                "table" => {
                    let pattern =
                        self.pattern::<IUIAutomationGridPattern>(&n, UIA_GridPatternId, b)?;
                    let rows = b.call(|| unsafe { pattern.CurrentRowCount() })?;
                    let columns = b.call(|| unsafe { pattern.CurrentColumnCount() })?;
                    property(&mut result, "rows", "GridPattern", json!(rows));
                    property(&mut result, "columns", "GridPattern", json!(columns));
                    let row = number(r, "row", 0);
                    let column = number(r, "column", 0);
                    let nr = number(r, "rows", 1);
                    let nc = number(r, "columns", 1);
                    if row >= rows.max(0) as usize || column >= columns.max(0) as usize {
                        return Err(invalid());
                    }
                    'cells: for y in row..row.saturating_add(nr).min(rows as usize) {
                        for x in column..column.saturating_add(nc).min(columns as usize) {
                            if result["nodes"].as_array().unwrap().len() >= b.limit("max_items") {
                                b.mark("item_limit");
                                break 'cells;
                            }
                            let cell = b.call(|| unsafe { pattern.GetItem(y as i32, x as i32) })?;
                            let sample = self.sample(&cell, b)?;
                            result["nodes"].as_array_mut().unwrap().push(sample);
                        }
                    }
                    if r["include_headers"].as_bool().unwrap_or(true)
                        && let Ok(table) =
                            self.pattern::<IUIAutomationTablePattern>(&n, UIA_TablePatternId, b)
                    {
                        let headers = b.call(|| unsafe { table.GetCurrentRowHeaders() })?;
                        self.read_elements(&headers, &mut result, b)?;
                        let headers = b.call(|| unsafe { table.GetCurrentColumnHeaders() })?;
                        self.read_elements(&headers, &mut result, b)?;
                        self.properties(
                            &n,
                            &mut result,
                            &[UIA_TableRowOrColumnMajorPropertyId],
                            b,
                        )?;
                    }
                    if r["include_selection"].as_bool().unwrap_or(false)
                        && let Ok(selection) = self.pattern::<IUIAutomationSelectionPattern>(
                            &n,
                            UIA_SelectionPatternId,
                            b,
                        )
                    {
                        let array = b.call(|| unsafe { selection.GetCurrentSelection() })?;
                        self.read_elements(&array, &mut result, b)?;
                    }
                }
                "item" => {
                    let name = BSTR::from(string(r, "name")?);
                    let item = match r["source"].as_str() {
                        Some("uia_item_container") => {
                            let pattern = self.pattern::<IUIAutomationItemContainerPattern>(
                                &n,
                                UIA_ItemContainerPatternId,
                                b,
                            )?;
                            let after = if r["after"].is_object() {
                                let after = self.node(&r["after"])?;
                                self.require_descendant(&after, &n, b)?;
                                Some(after)
                            } else {
                                None
                            };
                            let name = scalar_variant(&json!(string(r, "name")?))?;
                            optional(b.call(|| unsafe {
                                pattern.FindItemByProperty(
                                    after.as_ref(),
                                    UIA_NamePropertyId,
                                    &name,
                                )
                            }))?
                        }
                        Some("uia_spreadsheet") => {
                            let pattern = self.pattern::<IUIAutomationSpreadsheetPattern>(
                                &n,
                                UIA_SpreadsheetPatternId,
                                b,
                            )?;
                            optional(b.call(|| unsafe { pattern.GetItemByName(&name) }))?
                        }
                        _ => return Err(unsupported()),
                    };
                    if let Some(item) = item {
                        let sample = self.sample(&item, b)?;
                        result["nodes"].as_array_mut().unwrap().push(sample);
                    }
                }
                "navigate" => {
                    let walker = self.walker("raw", b)?;
                    let direction = string(r, "direction")?;
                    let next = optional(b.call(|| unsafe {
                        match direction {
                            "parent" => walker.GetParentElement(&n),
                            "first_child" => walker.GetFirstChildElement(&n),
                            "last_child" => walker.GetLastChildElement(&n),
                            "next_sibling" => walker.GetNextSiblingElement(&n),
                            "previous_sibling" => walker.GetPreviousSiblingElement(&n),
                            _ => Err(windows::core::Error::from_hresult(windows::core::HRESULT(
                                0x80070057u32 as i32,
                            ))),
                        }
                    }))?;
                    if let Some(n) = next {
                        let sample = self.sample(&n, b)?;
                        result["nodes"].as_array_mut().unwrap().push(sample);
                    }
                }
                "wait_for_input_idle" => {
                    let pattern =
                        self.pattern::<IUIAutomationWindowPattern>(&n, UIA_WindowPatternId, b)?;
                    let timeout = number(r, "timeout_ms", 1000).min(
                        b.deadline
                            .saturating_duration_since(Instant::now())
                            .as_millis() as usize,
                    );
                    let idle = b.call(|| unsafe { pattern.WaitForInputIdle(timeout as i32) })?;
                    property(
                        &mut result,
                        "input_idle",
                        "WindowPattern",
                        json!(idle.as_bool()),
                    );
                }
                "relations" => {
                    let requested = r["relations"].as_array().ok_or_else(invalid)?;
                    for (name, id) in [
                        ("labeled_by", UIA_LabeledByPropertyId),
                        ("controller_for", UIA_ControllerForPropertyId),
                        ("described_by", UIA_DescribedByPropertyId),
                        ("flows_to", UIA_FlowsToPropertyId),
                        ("flows_from", UIA_FlowsFromPropertyId),
                    ] {
                        if requested.is_empty() || requested.iter().any(|v| v == name) {
                            self.properties(&n, &mut result, &[id], b)?;
                        }
                    }
                }
                "properties" => {
                    for group in r["groups"].as_array().ok_or_else(invalid)? {
                        let ids: &[UIA_PROPERTY_ID] = match group.as_str() {
                            Some("identity") => &[
                                UIA_ProcessIdPropertyId,
                                UIA_AutomationIdPropertyId,
                                UIA_ClassNamePropertyId,
                                UIA_FrameworkIdPropertyId,
                                UIA_ControlTypePropertyId,
                                UIA_LocalizedControlTypePropertyId,
                                UIA_NamePropertyId,
                                UIA_HelpTextPropertyId,
                                UIA_ProviderDescriptionPropertyId,
                            ],
                            Some("state") => &[
                                UIA_IsEnabledPropertyId,
                                UIA_HasKeyboardFocusPropertyId,
                                UIA_IsKeyboardFocusablePropertyId,
                                UIA_IsOffscreenPropertyId,
                                UIA_IsPasswordPropertyId,
                                UIA_IsRequiredForFormPropertyId,
                                UIA_IsControlElementPropertyId,
                                UIA_IsContentElementPropertyId,
                            ],
                            Some("value") => &[
                                UIA_ValueValuePropertyId,
                                UIA_ValueIsReadOnlyPropertyId,
                                UIA_RangeValueValuePropertyId,
                                UIA_ToggleToggleStatePropertyId,
                                UIA_ExpandCollapseExpandCollapseStatePropertyId,
                            ],
                            Some("window") => &[
                                UIA_BoundingRectanglePropertyId,
                                UIA_ClickablePointPropertyId,
                                UIA_WindowCanMaximizePropertyId,
                                UIA_WindowCanMinimizePropertyId,
                                UIA_WindowIsModalPropertyId,
                                UIA_WindowIsTopmostPropertyId,
                                UIA_WindowWindowVisualStatePropertyId,
                                UIA_WindowWindowInteractionStatePropertyId,
                            ],
                            _ => return Err(unsupported()),
                        };
                        self.properties(&n, &mut result, ids, b)?;
                    }
                }
                "native_details" => {
                    let ids: &[UIA_PROPERTY_ID] = match string(r, "group")? {
                        "uia_patterns" => &[
                            UIA_IsInvokePatternAvailablePropertyId,
                            UIA_IsValuePatternAvailablePropertyId,
                            UIA_IsRangeValuePatternAvailablePropertyId,
                            UIA_IsSelectionPatternAvailablePropertyId,
                            UIA_IsSelectionItemPatternAvailablePropertyId,
                            UIA_IsScrollPatternAvailablePropertyId,
                            UIA_IsScrollItemPatternAvailablePropertyId,
                            UIA_IsTextPatternAvailablePropertyId,
                            UIA_IsTextPattern2AvailablePropertyId,
                            UIA_IsTextEditPatternAvailablePropertyId,
                            UIA_IsGridPatternAvailablePropertyId,
                            UIA_IsGridItemPatternAvailablePropertyId,
                            UIA_IsTablePatternAvailablePropertyId,
                            UIA_IsTableItemPatternAvailablePropertyId,
                            UIA_IsTogglePatternAvailablePropertyId,
                            UIA_IsExpandCollapsePatternAvailablePropertyId,
                            UIA_IsWindowPatternAvailablePropertyId,
                            UIA_IsTransformPatternAvailablePropertyId,
                            UIA_IsTransformPattern2AvailablePropertyId,
                            UIA_IsDockPatternAvailablePropertyId,
                            UIA_IsMultipleViewPatternAvailablePropertyId,
                            UIA_IsVirtualizedItemPatternAvailablePropertyId,
                            UIA_IsItemContainerPatternAvailablePropertyId,
                            UIA_IsSynchronizedInputPatternAvailablePropertyId,
                            UIA_IsAnnotationPatternAvailablePropertyId,
                            UIA_IsStylesPatternAvailablePropertyId,
                            UIA_IsDragPatternAvailablePropertyId,
                            UIA_IsDropTargetPatternAvailablePropertyId,
                            UIA_IsLegacyIAccessiblePatternAvailablePropertyId,
                            UIA_IsSpreadsheetPatternAvailablePropertyId,
                            UIA_IsSpreadsheetItemPatternAvailablePropertyId,
                        ],
                        "uia_annotation" => &[
                            UIA_AnnotationAnnotationTypeIdPropertyId,
                            UIA_AnnotationAnnotationTypeNamePropertyId,
                            UIA_AnnotationAuthorPropertyId,
                            UIA_AnnotationDateTimePropertyId,
                            UIA_AnnotationTargetPropertyId,
                        ],
                        "uia_styles" => &[
                            UIA_StylesStyleIdPropertyId,
                            UIA_StylesStyleNamePropertyId,
                            UIA_StylesFillColorPropertyId,
                            UIA_StylesFillPatternStylePropertyId,
                            UIA_StylesShapePropertyId,
                            UIA_StylesFillPatternColorPropertyId,
                            UIA_StylesExtendedPropertiesPropertyId,
                        ],
                        "uia_drag" => &[
                            UIA_DragIsGrabbedPropertyId,
                            UIA_DragDropEffectPropertyId,
                            UIA_DragDropEffectsPropertyId,
                            UIA_DragGrabbedItemsPropertyId,
                        ],
                        "uia_drop_target" => &[
                            UIA_DropTargetDropTargetEffectPropertyId,
                            UIA_DropTargetDropTargetEffectsPropertyId,
                        ],
                        "uia_legacy" => &[
                            UIA_LegacyIAccessibleChildIdPropertyId,
                            UIA_LegacyIAccessibleNamePropertyId,
                            UIA_LegacyIAccessibleValuePropertyId,
                            UIA_LegacyIAccessibleDescriptionPropertyId,
                            UIA_LegacyIAccessibleRolePropertyId,
                            UIA_LegacyIAccessibleStatePropertyId,
                            UIA_LegacyIAccessibleHelpPropertyId,
                            UIA_LegacyIAccessibleKeyboardShortcutPropertyId,
                            UIA_LegacyIAccessibleSelectionPropertyId,
                            UIA_LegacyIAccessibleDefaultActionPropertyId,
                        ],
                        "uia_transform" => &[
                            UIA_TransformCanMovePropertyId,
                            UIA_TransformCanResizePropertyId,
                            UIA_TransformCanRotatePropertyId,
                            UIA_Transform2CanZoomPropertyId,
                            UIA_Transform2ZoomLevelPropertyId,
                            UIA_Transform2ZoomMinimumPropertyId,
                            UIA_Transform2ZoomMaximumPropertyId,
                        ],
                        "uia_views" => &[
                            UIA_MultipleViewCurrentViewPropertyId,
                            UIA_MultipleViewSupportedViewsPropertyId,
                        ],
                        "uia_spreadsheet_item" => &[
                            UIA_SpreadsheetItemFormulaPropertyId,
                            UIA_SpreadsheetItemAnnotationObjectsPropertyId,
                            UIA_SpreadsheetItemAnnotationTypesPropertyId,
                        ],
                        _ => return Err(unsupported()),
                    };
                    self.properties(&n, &mut result, ids, b)?;
                }
                _ => return Err(unsupported()),
            }
            Ok(())
        })();
        partial(outcome, b)?;
        result["completeness"] = b.completeness();
        Ok(result)
    }
    pub(super) fn read_elements(
        &mut self,
        array: &IUIAutomationElementArray,
        result: &mut Value,
        b: &Budget,
    ) -> Result<(), EIPError> {
        let count = b.call(|| unsafe { array.Length() })?;
        for index in 0..count {
            if result["nodes"].as_array().unwrap().len() >= b.limit("max_items").min(512) {
                b.mark("item_limit");
                break;
            }
            let node = b.call(|| unsafe { array.GetElement(index) })?;
            let sample = self.sample(&node, b)?;
            result["nodes"].as_array_mut().unwrap().push(sample);
        }
        Ok(())
    }
    fn properties(
        &mut self,
        n: &IUIAutomationElement,
        result: &mut Value,
        ids: &[UIA_PROPERTY_ID],
        b: &Budget,
    ) -> Result<(), EIPError> {
        for id in ids {
            if result["properties"].as_array().unwrap().len() >= b.limit("max_items").min(128) {
                b.mark("item_limit");
                break;
            }
            let name = b
                .call(|| unsafe { self.automation.GetPropertyProgrammaticName(*id) })?
                .to_string();
            match b.call(|| unsafe { n.GetCurrentPropertyValueEx(*id, true) }) {
                Ok(value) => {
                    let value = self.variant_value(value, b)?;
                    property_value(result, &name, "UIA", value);
                }
                Err(e) => field_error(result, &name, &e),
            }
        }
        Ok(())
    }
    pub(super) fn variant_value(&mut self, raw: VARIANT, b: &Budget) -> Result<Value, EIPError> {
        let value = OwnedVariant(raw);
        unsafe {
            let v = &value.Anonymous.Anonymous;
            let data = &v.Anonymous;
            Ok(match v.vt {
                VT_EMPTY | VT_NULL => json!({"kind":"unavailable"}),
                VT_BOOL => json!({"kind":"scalar","value":data.boolVal.0!=0}),
                VT_I4 | VT_INT => json!({"kind":"scalar","value":data.lVal}),
                VT_UI4 | VT_UINT => json!({"kind":"scalar","value":data.ulVal}),
                VT_I2 => json!({"kind":"scalar","value":data.iVal}),
                VT_R8 if data.dblVal.is_finite() => json!({"kind":"scalar","value":data.dblVal}),
                VT_BSTR => json!({"kind":"scalar","value":b.text(data.bstrVal.to_string())}),
                VT_UNKNOWN => {
                    let Some(object) = data.punkVal.as_ref() else {
                        return Ok(json!({"kind":"unavailable"}));
                    };
                    let mixed = b.call(|| self.automation.ReservedMixedAttributeValue())?;
                    let unsupported = b.call(|| self.automation.ReservedNotSupportedValue())?;
                    if object == &mixed {
                        json!({"kind":"mixed"})
                    } else if object == &unsupported {
                        json!({"kind":"unsupported"})
                    } else if let Ok(node) = b.call(|| object.cast::<IUIAutomationElement>()) {
                        json!({"kind":"nodes","values":[self.reference(node)?]})
                    } else if let Ok(array) = b.call(|| object.cast::<IUIAutomationElementArray>())
                    {
                        let count = b.call(|| array.Length())?;
                        let mut nodes = Vec::new();
                        for i in 0..count.min(b.limit("max_items").min(512) as i32) {
                            nodes.push(self.reference(b.call(|| array.GetElement(i))?)?);
                        }
                        if count as usize > nodes.len() {
                            b.mark("item_limit");
                        }
                        json!({"kind":"nodes","values":nodes})
                    } else {
                        json!({"kind":"unsupported"})
                    }
                }
                vt if vt.0 == VT_ARRAY.0 | VT_I4.0 => {
                    let array = BorrowedArray(data.parray);
                    json!({"kind":"scalars","values":array.integers(b)?})
                }
                vt if vt.0 == VT_ARRAY.0 | VT_R8.0 => {
                    let array = BorrowedArray(data.parray);
                    json!({"kind":"scalars","values":array.doubles(b)?})
                }
                vt if vt.0 == VT_ARRAY.0 | VT_UNKNOWN.0 => {
                    let array = BorrowedArray(data.parray);
                    let mut nodes = Vec::new();
                    let outcome = (|| {
                        for index in array.indices(VT_UNKNOWN, b, 1)? {
                            let mut object: Option<windows::core::IUnknown> = None;
                            b.call(|| {
                                SafeArrayGetElement(
                                    array.0,
                                    &index,
                                    (&mut object as *mut Option<windows::core::IUnknown>).cast(),
                                )
                            })?;
                            if let Some(object) = object {
                                nodes.push(self.reference(
                                    b.call(|| object.cast::<IUIAutomationElement>())?,
                                )?);
                            }
                        }
                        Ok(())
                    })();
                    partial(outcome, b)?;
                    json!({"kind":"nodes","values":nodes})
                }
                vt if vt.0 == VT_ARRAY.0 | VT_BSTR.0 => {
                    let array = BorrowedArray(data.parray);
                    let mut values = Vec::new();
                    for index in array.indices(VT_BSTR, b, 1)? {
                        let mut text = BSTR::new();
                        b.call(|| {
                            SafeArrayGetElement(array.0, &index, (&mut text as *mut BSTR).cast())
                        })?;
                        values.push(b.text(text.to_string()));
                    }
                    json!({"kind":"scalars","values":values})
                }
                _ => json!({"kind":"unsupported"}),
            })
        }
    }
}
pub(super) fn property(result: &mut Value, name: &str, source: &str, value: Value) {
    property_value(result, name, source, json!({"kind":"scalar","value":value}));
}
pub(super) fn property_value(result: &mut Value, name: &str, source: &str, value: Value) {
    result["properties"]
        .as_array_mut()
        .unwrap()
        .push(json!({"name":name,"source":source,"value":value}));
}
pub(super) struct OwnedVariant(pub(super) VARIANT);
impl Deref for OwnedVariant {
    type Target = VARIANT;
    fn deref(&self) -> &VARIANT {
        &self.0
    }
}
impl Drop for OwnedVariant {
    fn drop(&mut self) {
        unsafe {
            let _ = VariantClear(&mut self.0);
        }
    }
}
pub(super) fn scalar_variant(value: &Value) -> Result<OwnedVariant, EIPError> {
    let mut raw = VARIANT::default();
    unsafe {
        let v = &mut raw.Anonymous.Anonymous;
        if let Some(s) = value.as_str() {
            v.vt = VT_BSTR;
            v.Anonymous.bstrVal = ManuallyDrop::new(BSTR::from(s));
        } else if let Some(b) = value.as_bool() {
            v.vt = VT_BOOL;
            v.Anonymous.boolVal = windows::Win32::Foundation::VARIANT_BOOL(if b { -1 } else { 0 });
        } else if let Some(i) = value.as_i64().and_then(|v| i32::try_from(v).ok()) {
            v.vt = VT_I4;
            v.Anonymous.lVal = i;
        } else if let Some(n) = value.as_f64().filter(|v| v.is_finite()) {
            v.vt = VT_R8;
            v.Anonymous.dblVal = n;
        } else {
            return Err(invalid());
        }
    }
    Ok(OwnedVariant(raw))
}
pub(super) struct OwnedArray(pub(super) *mut SAFEARRAY);
impl Drop for OwnedArray {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                let _ = SafeArrayDestroy(self.0);
            }
        }
    }
}
impl OwnedArray {
    pub(super) fn integers(&self, b: &Budget) -> Result<Vec<i32>, EIPError> {
        BorrowedArray(self.0).integers(b)
    }
    pub(super) fn rectangles(&self, b: &Budget) -> Result<Vec<f64>, EIPError> {
        BorrowedArray(self.0).double_items(b, 4)
    }
}
struct BorrowedArray(*mut SAFEARRAY);
impl BorrowedArray {
    fn indices(
        &self,
        expected: VARENUM,
        b: &Budget,
        width: usize,
    ) -> Result<std::ops::Range<i32>, EIPError> {
        if self.0.is_null() {
            return Ok(0..0);
        }
        if unsafe { SafeArrayGetDim(self.0) } != 1 {
            return Err(error(
                ErrorType::ProviderUnavailable,
                "UIA array must be one-dimensional",
            ));
        }
        let actual = b.call(|| unsafe { SafeArrayGetVartype(self.0) })?;
        if actual != expected {
            return Err(error(
                ErrorType::ProviderUnavailable,
                "unexpected UIA array element type",
            ));
        }
        let start = b.call(|| unsafe { SafeArrayGetLBound(self.0, 1) })?;
        let end = b.call(|| unsafe { SafeArrayGetUBound(self.0, 1) })?;
        let count = (i64::from(end) - i64::from(start) + 1).max(0) as usize;
        if !count.is_multiple_of(width) {
            return Err(error(
                ErrorType::ProviderUnavailable,
                "incomplete UIA array item",
            ));
        }
        let max = b.limit("max_items").min(512) * width;
        if count > max {
            b.mark("item_limit");
        }
        Ok(start
            ..start
                .checked_add(count.min(max) as i32)
                .ok_or_else(invalid)?)
    }
    fn integers(&self, b: &Budget) -> Result<Vec<i32>, EIPError> {
        let mut result = Vec::new();
        for index in self.indices(VT_I4, b, 1)? {
            let mut v = 0i32;
            b.call(|| unsafe { SafeArrayGetElement(self.0, &index, (&mut v as *mut i32).cast()) })?;
            result.push(v);
        }
        Ok(result)
    }
    fn doubles(&self, b: &Budget) -> Result<Vec<f64>, EIPError> {
        self.double_items(b, 1)
    }
    fn double_items(&self, b: &Budget, width: usize) -> Result<Vec<f64>, EIPError> {
        let mut result = Vec::new();
        for index in self.indices(VT_R8, b, width)? {
            let mut v = 0.0f64;
            if let Err(e) = b.call(|| unsafe {
                SafeArrayGetElement(self.0, &index, (&mut v as *mut f64).cast())
            }) {
                if matches!(b.reason.get(), Some("deadline" | "call_limit")) {
                    break;
                }
                return Err(e);
            }
            if !v.is_finite() {
                return Err(error(
                    ErrorType::ProviderUnavailable,
                    "non-finite UIA array value",
                ));
            }
            result.push(v);
        }
        result.truncate(result.len() / width * width);
        Ok(result)
    }
}
