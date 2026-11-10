//! Bounded, type-aware AX observations. Reads expose native availability and
//! observed writability, with protected payloads omitted before acquisition.
use super::*;
use objc2_core_foundation::{CFBoolean, CFURL};
impl Native {
    pub(super) fn read(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let owner = &p["node"];
        let n = self.node(owner)?;
        let r = &p["request"];
        let kind = string(r, "kind")?;
        let mut out = json!({"node":owner,"sampled_at":now(),"completeness":{"complete":true},"nodes":[],"properties":[],"ranges":[],"attributes":[],"field_errors":[]});
        if kind != "navigate" && self.protected(&n, b)? {
            out["text"] = json!({"status":"protected","source":"AX"});
            return Ok(out);
        }
        let result = (|| {
            match kind {
                "properties" => {
                    for group in r["groups"].as_array().ok_or_else(invalid)? {
                        let keys: &[&str] = match group.as_str() {
                            Some("identity") => &[
                                "AXRole",
                                "AXSubrole",
                                "AXTitle",
                                "AXDescription",
                                "AXIdentifier",
                                "AXHelp",
                            ],
                            Some("state") => &[
                                "AXEnabled",
                                "AXFocused",
                                "AXSelected",
                                "AXExpanded",
                                "AXDisclosing",
                                "AXContainsProtectedContent",
                            ],
                            Some("value") => {
                                &["AXValue", "AXMinValue", "AXMaxValue", "AXValueDescription"]
                            }
                            Some("window") => &[
                                "AXMain",
                                "AXMinimized",
                                "AXModal",
                                "AXCloseButton",
                                "AXZoomButton",
                                "AXMinimizeButton",
                            ],
                            _ => return Err(invalid()),
                        };
                        self.properties(&n, keys, &mut out, b)?;
                    }
                }
                "numeric_value" => self.properties(
                    &n,
                    &["AXValue", "AXMinValue", "AXMaxValue", "AXValueIncrement"],
                    &mut out,
                    b,
                )?,
                "document" => self.properties(
                    &n,
                    &["AXDocument", "AXURL", "AXFilename", "AXEdited"],
                    &mut out,
                    b,
                )?,
                "native_details" => {
                    let parameterized = match r["group"].as_str() {
                        Some("ax_attributes") => false,
                        Some("ax_parameterized_attributes") => true,
                        _ => return Err(unsupported()),
                    };
                    let names = native::names(
                        &n,
                        if parameterized {
                            "parameters"
                        } else {
                            "attributes"
                        },
                        b,
                    )?;
                    if names.len() > b.limit("max_items") {
                        b.mark("item_limit");
                    }
                    for i in 0..names.len().min(b.limit("max_items")) {
                        let name = native::name(&*names.get(i).ok_or_else(unavailable)?, b)?;
                        if name.len() > 1024 {
                            continue;
                        }
                        let mut metadata = json!({"name":name,"parameterized":parameterized});
                        if !parameterized {
                            match native::writable(&n, &name, b) {
                                Ok(v) => metadata["writable"] = json!(v),
                                Err(e) => field_error(&mut out, &name, &e),
                            }
                        }
                        out["attributes"].as_array_mut().unwrap().push(metadata);
                    }
                }
                "text" => {
                    let length = self.text_length(&n, b);
                    match length {
                        Ok(length) => {
                            let span = if r["span"].is_null() {
                                CFRange {
                                    location: 0,
                                    length,
                                }
                            } else {
                                let s = &r["span"];
                                if s["unit"] != "ax_native" {
                                    return Err(invalid());
                                }
                                let start = s["start"]
                                    .as_u64()
                                    .and_then(|v| isize::try_from(v).ok())
                                    .ok_or_else(invalid)?;
                                let end = s["end"]
                                    .as_u64()
                                    .and_then(|v| isize::try_from(v).ok())
                                    .ok_or_else(invalid)?;
                                let span = CFRange {
                                    location: start,
                                    length: end.checked_sub(start).ok_or_else(invalid)?,
                                };
                                range::validate_span(span, length)?;
                                span
                            };
                            out["text"] = self.text_span(&n, span, b)?;
                        }
                        Err(e)
                            if e.data.error_type == ErrorType::Unsupported
                                && r["span"].is_null() =>
                        {
                            let value = get(&n, "AXValue", b)?;
                            let (text, _) = native::text(&value, b)?;
                            out["text"] = json!({"status":if text.is_empty(){"empty"}else{"text"},"source":"AXValue","text":text});
                        }
                        Err(e) => return Err(e),
                    }
                }
                "selection" => {
                    for key in ["AXSelectedChildren", "AXSelectedRows", "AXSelectedColumns"] {
                        self.collection_property(&n, key, &mut out, b)?;
                    }
                    match self.selected_spans(&n, b) {
                        Ok(spans) => self.append_ranges(owner, spans, &mut out, b)?,
                        // A list/table can support selected nodes without text
                        // selection. Preserve those properties and report the
                        // unsupported text field instead of fabricating empty.
                        Err(e) if e.data.error_type == ErrorType::Unsupported => {
                            field_error(&mut out, "AXSelectedTextRange", &e);
                        }
                        Err(e) => return Err(e),
                    }
                }
                "navigate" => {
                    let direction = string(r, "direction")?;
                    let target = match direction {
                        "parent" => optional(get(&n, "AXParent", b))?
                            .map(typed::<AXUIElement>)
                            .transpose()?,
                        "first_child" | "last_child" => {
                            let count = native::count(&n, "AXChildren", b)?;
                            if count == 0 {
                                None
                            } else {
                                let i = if direction == "first_child" {
                                    0
                                } else {
                                    count - 1
                                };
                                native::page(&n, "AXChildren", i, 1, b)?
                                    .get(0)
                                    .map(typed)
                                    .transpose()?
                            }
                        }
                        "next_sibling" | "previous_sibling" => {
                            let parent = typed::<AXUIElement>(get(&n, "AXParent", b)?)?;
                            let count = native::count(&parent, "AXChildren", b)?;
                            let mut found = None;
                            for i in 0..count {
                                let page = native::page(&parent, "AXChildren", i, 1, b)?;
                                if let Some(value) = page.get(0) {
                                    let child = typed::<AXUIElement>(value)?;
                                    if child == n {
                                        let target = if direction == "next_sibling" {
                                            i.checked_add(1).filter(|i| *i < count)
                                        } else {
                                            i.checked_sub(1)
                                        };
                                        if let Some(i) = target {
                                            found = native::page(&parent, "AXChildren", i, 1, b)?
                                                .get(0)
                                                .map(typed)
                                                .transpose()?;
                                        }
                                        break;
                                    }
                                }
                            }
                            found
                        }
                        _ => return Err(invalid()),
                    };
                    if let Some(n) = target
                        && let Some(node) = self.sample(&n, b)?
                    {
                        out["nodes"].as_array_mut().unwrap().push(node);
                    }
                }
                "table" => {
                    self.properties(&n, &["AXRowCount", "AXColumnCount"], &mut out, b)?;
                    let row = number(r, "row", 0);
                    let column = number(r, "column", 0);
                    let nr = number(r, "rows", 1);
                    let nc = number(r, "columns", 1);
                    'rows: for y in row..row + nr {
                        for x in column..column + nc {
                            if !collection_room(&out, "nodes", b) {
                                break 'rows;
                            }
                            if let Some(cell) = self.table_cell(&n, x, y, b)? {
                                let Some(node) = self.sample(&cell, b)? else {
                                    break 'rows;
                                };
                                out["nodes"].as_array_mut().unwrap().push(node);
                            }
                        }
                    }
                    if r["include_headers"] != false {
                        for key in ["AXRowHeaderUIElements", "AXColumnHeaderUIElements"] {
                            self.collection_property(&n, key, &mut out, b)?;
                        }
                    }
                    if r["include_selection"] == true {
                        for key in ["AXSelectedRows", "AXSelectedColumns", "AXSelectedChildren"] {
                            self.collection_property(&n, key, &mut out, b)?;
                        }
                    }
                }
                "scroll" => {
                    for key in ["AXHorizontalScrollBar", "AXVerticalScrollBar"] {
                        if !collection_room(&out, "nodes", b) {
                            break;
                        }
                        if let Some(v) = optional(get(&n, key, b))? {
                            let bar = typed(v)?;
                            let Some(node) = self.sample(&bar, b)? else {
                                break;
                            };
                            out["nodes"].as_array_mut().unwrap().push(node);
                            self.properties(
                                &bar,
                                &["AXValue", "AXMinValue", "AXMaxValue"],
                                &mut out,
                                b,
                            )?;
                        }
                    }
                }
                "relations" => {
                    let supported = [
                        "AXTitleUIElement",
                        "AXServesAsTitleForUIElements",
                        "AXLinkedUIElements",
                        "AXLabelUIElements",
                        "AXDetailsElements",
                        "AXErrorMessageElements",
                        "AXWindow",
                        "AXTopLevelUIElement",
                    ];
                    let requested = r["relations"].as_array().ok_or_else(invalid)?;
                    let keys: Vec<_> = if requested.is_empty() {
                        supported.to_vec()
                    } else {
                        requested
                            .iter()
                            .map(|v| {
                                v.as_str()
                                    .filter(|k| supported.contains(k))
                                    .ok_or_else(unsupported)
                            })
                            .collect::<Result<_, _>>()?
                    };
                    self.properties(&n, &keys, &mut out, b)?;
                }
                _ => return Err(unsupported()),
            }
            Ok(())
        })();
        partial(result, b)?;
        out["completeness"] = b.completeness();
        Ok(out)
    }
    fn table_cell(
        &self,
        table: &AXUIElement,
        column: usize,
        row: usize,
        b: &Budget,
    ) -> Result<Option<CFRetained<AXUIElement>>, EIPError> {
        let args = objc2_core_foundation::CFArray::from_retained_objects(&[
            CFNumber::new_isize(column as isize),
            CFNumber::new_isize(row as isize),
        ]);
        match native::parameter(table, "AXCellForColumnAndRow", &args, b) {
            Ok(value) => return typed(value).map(Some),
            Err(e) if e.data.error_type == ErrorType::Unsupported => {}
            Err(e) => return optional(Err(e)),
        }
        // AppKit NSTableView exposes rows with ordered cell children, but does
        // not necessarily implement AXCellForColumnAndRow. Use that native
        // structure only when the row has one child per observed column; merged
        // or sparse rows cannot safely be indexed this way.
        let rows = native::count(table, "AXRows", b)?;
        let columns = native::count(table, "AXColumns", b)?;
        if row >= rows || column >= columns {
            return Ok(None);
        }
        let rows = native::page(table, "AXRows", row, 1, b)?;
        let row = typed::<AXUIElement>(rows.get(0).ok_or_else(unavailable)?)?;
        if !native::string_is(&*get(&row, "AXRole", b)?, "AXRow")?
            || native::count(&row, "AXChildren", b)? != columns
        {
            return Err(unsupported());
        }
        let cells = native::page(&row, "AXChildren", column, 1, b)?;
        typed(cells.get(0).ok_or_else(unavailable)?).map(Some)
    }
    fn property_value(
        &mut self,
        n: &AXUIElement,
        key: &str,
        b: &Budget,
    ) -> Result<(Value, String), EIPError> {
        match get(n, key, b) {
            Ok(value) => self.value(&value, b).map(|v| (v, key.into())),
            Err(e)
                if e.data.error_type == ErrorType::Unsupported
                    && matches!(key, "AXRowCount" | "AXColumnCount") =>
            {
                let source = if key == "AXRowCount" {
                    "AXRows"
                } else {
                    "AXColumns"
                };
                let count = native::count(n, source, b)?;
                Ok((json!({"kind":"scalar","value":count}), source.into()))
            }
            Err(e) => Err(e),
        }
    }
    fn properties(
        &mut self,
        n: &AXUIElement,
        keys: &[&str],
        out: &mut Value,
        b: &Budget,
    ) -> Result<(), EIPError> {
        for key in keys {
            b.check()?;
            if out["properties"].as_array().unwrap().len() >= b.limit("max_items") {
                b.mark("item_limit");
                break;
            }
            match self.property_value(n, key, b) {
                Ok((value, source)) => out["properties"]
                    .as_array_mut()
                    .unwrap()
                    .push(json!({"name":key,"source":source,"value":value})),
                Err(e) => field_error(out, key, &e),
            }
        }
        Ok(())
    }
    fn collection_property(
        &mut self,
        n: &AXUIElement,
        key: &str,
        out: &mut Value,
        b: &Budget,
    ) -> Result<(), EIPError> {
        if !collection_room(out, "properties", b) {
            return Ok(());
        }
        let total = match optional(native::count(n, key, b))? {
            Some(v) => v,
            None => return Ok(()),
        };
        let max = b.limit("max_items").min(512);
        let mut values = Vec::new();
        if total > max {
            b.mark("item_limit");
        }
        for i in 0..total.min(max) {
            if let Some(v) = native::page(n, key, i, 1, b)?.get(0) {
                let node = typed(v)?;
                values.push(self.reference(node)?);
            }
        }
        out["properties"]
            .as_array_mut()
            .unwrap()
            .push(json!({"name":key,"source":key,"value":{"kind":"nodes","values":values}}));
        Ok(())
    }
    pub(super) fn value(&mut self, v: &CFType, b: &Budget) -> Result<Value, EIPError> {
        if let Some(v) = v.downcast_ref::<CFString>() {
            return Ok(json!({"kind":"scalar","value":native::name(v,b)?}));
        }
        if let Some(v) = v.downcast_ref::<CFBoolean>() {
            return Ok(json!({"kind":"scalar","value":v.value()}));
        }
        if let Some(v) = v.downcast_ref::<CFNumber>() {
            let v = if v.is_float_type() {
                json!(
                    v.as_f64()
                        .filter(|v| v.is_finite())
                        .ok_or_else(unavailable)?
                )
            } else {
                json!(v.as_isize().ok_or_else(unavailable)?)
            };
            return Ok(json!({"kind":"scalar","value":v}));
        }
        if let Some(v) = v.downcast_ref::<CFURL>() {
            return Ok(json!({"kind":"scalar","value":native::name(&v.string(),b)?}));
        }
        if let Some(v) = v.downcast_ref::<AXUIElement>() {
            let node = unsafe { CFRetained::retain(NonNull::from(v)) };
            return Ok(json!({"kind":"nodes","values":[self.reference(node)?]}));
        }
        if let Ok(a) = native::array(v) {
            let max = a.len().min(b.limit("max_items")).min(512);
            if a.len() > max {
                b.mark("item_limit");
            }
            let mut nodes = Vec::new();
            let mut scalars = Vec::new();
            for i in 0..max {
                let v = a.get(i).ok_or_else(unavailable)?;
                if let Some(n) = v.downcast_ref::<AXUIElement>() {
                    nodes.push(self.reference(unsafe { CFRetained::retain(NonNull::from(n)) })?);
                } else if v.downcast_ref::<objc2_core_foundation::CFArray>().is_some() {
                    return Err(unsupported());
                } else {
                    let value = self.value(&v, b)?;
                    if value["kind"] != "scalar" {
                        return Err(unsupported());
                    }
                    scalars.push(value["value"].clone());
                }
            }
            if !nodes.is_empty() && !scalars.is_empty() {
                return Err(unsupported());
            }
            return Ok(if scalars.is_empty() {
                json!({"kind":"nodes","values":nodes})
            } else {
                json!({"kind":"scalars","values":scalars})
            });
        }
        Ok(json!({"kind":"unsupported"}))
    }
}
