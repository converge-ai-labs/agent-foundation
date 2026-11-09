//! Native UIA endpoints remain opaque. Derivations clone the source range and
//! cannot mutate an older reference's endpoints or the application's selection.
use super::*;
impl Native {
    pub(super) fn range_operand(
        &mut self,
        r: &Value,
        owner: &Value,
    ) -> Result<IUIAutomationTextRange, EIPError> {
        self.node(owner)?;
        if r["node"] != *owner || r["unit"] != "uia_opaque" {
            return Err(stale());
        }
        let id = string(r, "range_handle")?;
        let entry = self.handles.get_mut(id).ok_or_else(stale)?;
        entry.touched = Instant::now();
        match &entry.value {
            Handle::Range {
                owner: handle,
                value,
            } if owner["node_handle"] == *handle => Ok(value.clone()),
            _ => Err(stale()),
        }
    }
    fn range_reference(
        &mut self,
        owner: &Value,
        value: IUIAutomationTextRange,
    ) -> Result<Value, EIPError> {
        let id = self.put(
            "ar",
            Handle::Range {
                owner: string(owner, "node_handle")?.into(),
                value,
            },
        )?;
        Ok(json!({"node":owner,"range_handle":id,"unit":"uia_opaque"}))
    }
    fn range_result(&self, node: &Value) -> Value {
        json!({"node":node,"sampled_at":now(),"completeness":{"complete":true},"ranges":[],"nodes":[],"bounds":[],"properties":[],"attribute_runs":[],"field_errors":[]})
    }
    pub(super) fn range(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let owner = &p["node"];
        let n = self.node(owner)?;
        let mut result = self.range_result(owner);
        if self.protected(&n, b)? {
            result["text"] = json!({"status":"protected","source":"UIA.TextPattern"});
            return Ok(result);
        }
        let q = &p["query"];
        let kind = string(q, "kind")?;
        let pattern = self.pattern::<IUIAutomationTextPattern>(&n, UIA_TextPatternId, b)?;
        let mut ranges = Vec::new();
        let outcome = (|| {
            match kind {
                "document" => ranges.push(b.call(|| unsafe { pattern.DocumentRange() })?),
                "selection" | "visible" => {
                    let array = b.call(|| unsafe {
                        if kind == "selection" {
                            pattern.GetSelection()
                        } else {
                            pattern.GetVisibleRanges()
                        }
                    })?;
                    let count = b.call(|| unsafe { array.Length() })?;
                    for i in 0..count.min(b.limit("max_items").min(256) as i32) {
                        ranges.push(b.call(|| unsafe { array.GetElement(i) })?);
                    }
                    if count as usize > b.limit("max_items").min(256) {
                        b.mark("item_limit");
                    }
                }
                "caret" => {
                    let pattern =
                        self.pattern::<IUIAutomationTextPattern2>(&n, UIA_TextPattern2Id, b)?;
                    let mut active = windows::core::BOOL(0);
                    ranges.push(b.call(|| unsafe { pattern.GetCaretRange(&mut active) })?);
                }
                "active_composition" | "conversion_target" => {
                    let pattern =
                        self.pattern::<IUIAutomationTextEditPattern>(&n, UIA_TextEditPatternId, b)?;
                    if let Some(range) = optional(b.call(|| unsafe {
                        if kind == "active_composition" {
                            pattern.GetActiveComposition()
                        } else {
                            pattern.GetConversionTarget()
                        }
                    }))? {
                        ranges.push(range);
                    }
                }
                "point" => {
                    let point = native_point(&q["point"])?;
                    ranges.push(b.call(|| unsafe { pattern.RangeFromPoint(point) })?);
                }
                "child" => {
                    let child = self.node(&q["child"])?;
                    self.require_descendant(&child, &n, b)?;
                    ranges.push(b.call(|| unsafe { pattern.RangeFromChild(&child) })?);
                }
                "expand" | "move" | "move_endpoint" | "endpoint_by_range" | "find_text"
                | "find_attribute" => {
                    let source = self.range_operand(&p["source"], owner)?;
                    let range = b.call(|| unsafe { source.Clone() })?;
                    match kind {
                        "expand" => {
                            let unit = text_unit(string(q, "unit")?)?;
                            b.call(|| unsafe { range.ExpandToEnclosingUnit(unit) })?;
                        }
                        "move" => {
                            let unit = text_unit(string(q, "unit")?)?;
                            let count = signed(q, "count")?;
                            result["actual_movement"] =
                                json!(b.call(|| unsafe { range.Move(unit, count) })?);
                        }
                        "move_endpoint" => {
                            let endpoint = endpoint(string(q, "endpoint")?)?;
                            let unit = text_unit(string(q, "unit")?)?;
                            let count = signed(q, "count")?;
                            result["actual_movement"] = json!(b.call(|| unsafe {
                                range.MoveEndpointByUnit(endpoint, unit, count)
                            })?);
                        }
                        "endpoint_by_range" => {
                            let other = self.range_operand(&q["other"], owner)?;
                            let from = endpoint(string(q, "endpoint")?)?;
                            let to = endpoint(string(q, "other_endpoint")?)?;
                            b.call(|| unsafe { range.MoveEndpointByRange(from, &other, to) })?;
                        }
                        "find_text" => {
                            let text = BSTR::from(string(q, "text")?);
                            if let Some(found) = optional(b.call(|| unsafe {
                                range.FindText(
                                    &text,
                                    q["backward"].as_bool().unwrap_or(false),
                                    q["ignore_case"].as_bool().unwrap_or(false),
                                )
                            }))? {
                                ranges.push(found);
                            }
                            return Ok(());
                        }
                        "find_attribute" => {
                            let id = UIA_TEXTATTRIBUTE_ID(signed(q, "attribute_id")?);
                            let value = scalar_variant(&q["value"]["value"])?;
                            if let Some(found) = optional(b.call(|| unsafe {
                                range.FindAttribute(
                                    id,
                                    &value,
                                    q["backward"].as_bool().unwrap_or(false),
                                )
                            }))? {
                                ranges.push(found);
                            }
                            return Ok(());
                        }
                        _ => unreachable!(),
                    }
                    ranges.push(range);
                }
                // UIA does not expose stable integer offsets or line indices.
                "span" | "index" | "line" | "line_for_index" => return Err(unsupported()),
                _ => return Err(unsupported()),
            }
            Ok(())
        })();
        partial(outcome, b)?;
        for range in ranges {
            let reference = self.range_reference(owner, range)?;
            result["ranges"]
                .as_array_mut()
                .unwrap()
                .push(reference.clone());
            if !b.fits("computer.a11y_range", &result)? {
                self.handles.remove(string(&reference, "range_handle")?);
                result["ranges"].as_array_mut().unwrap().pop();
                b.mark("byte_limit");
                break;
            }
        }
        result["completeness"] = b.completeness();
        Ok(result)
    }
    pub(super) fn range_read(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let reference = &p["reference"];
        let owner = &reference["node"];
        let n = self.node(owner)?;
        let range = self.range_operand(reference, owner)?;
        let mut result = self.range_result(owner);
        if self.protected(&n, b)? {
            result["text"] = json!({"status":"protected","source":"UIA.TextPattern"});
            return Ok(result);
        }
        let r = &p["request"];
        let kind = string(r, "kind")?;
        let outcome = (|| {
            match kind {
                "text" => result["text"] = self.range_text(&range, b)?,
                "bounds" => {
                    let array = OwnedArray(b.call(|| unsafe { range.GetBoundingRectangles() })?);
                    let values = array.rectangles(b)?;
                    if values.len() % 4 != 0 {
                        return Err(error(
                            ErrorType::ProviderUnavailable,
                            "invalid UIA rectangle array",
                        ));
                    }
                    for rect in values.chunks_exact(4).take(b.limit("max_items").min(512)) {
                        if rect.iter().all(|v| v.is_finite()) && rect[2] >= 0.0 && rect[3] >= 0.0 {
                            result["bounds"].as_array_mut().unwrap().push(json!({"frame":"uia_physical_screen","x":rect[0],"y":rect[1],"width":rect[2],"height":rect[3]}));
                        }
                    }
                    if values.len() / 4 > b.limit("max_items").min(512) {
                        b.mark("item_limit");
                    }
                }
                "children" => {
                    let array = b.call(|| unsafe { range.GetChildren() })?;
                    self.read_elements(&array, &mut result, b)?;
                }
                "enclosing_element" => {
                    let n = b.call(|| unsafe { range.GetEnclosingElement() })?;
                    let sample = self.sample(&n, b)?;
                    result["nodes"].as_array_mut().unwrap().push(sample);
                }
                "attributes" => {}
                "compare" => {
                    let other = self.range_operand(&r["other"], owner)?;
                    result["comparison"] =
                        if r["endpoint"].is_null() && r["other_endpoint"].is_null() {
                            json!(b.call(|| unsafe { range.Compare(&other) })?.as_bool())
                        } else {
                            let a = endpoint(string(r, "endpoint")?)?;
                            let z = endpoint(string(r, "other_endpoint")?)?;
                            json!(b.call(|| unsafe { range.CompareEndpoints(a, &other, z) })?)
                        };
                }
                _ => return Err(unsupported()),
            }
            if matches!(kind, "attributes" | "text") {
                let ids = r["attribute_ids"].as_array().ok_or_else(invalid)?;
                if ids.len() > b.limit("max_items").min(128) {
                    b.mark("item_limit");
                }
                for id in r["attribute_ids"]
                    .as_array()
                    .ok_or_else(invalid)?
                    .iter()
                    .take(b.limit("max_items").min(128))
                {
                    let id = id
                        .as_i64()
                        .and_then(|v| i32::try_from(v).ok())
                        .ok_or_else(invalid)?;
                    let value =
                        b.call(|| unsafe { range.GetAttributeValue(UIA_TEXTATTRIBUTE_ID(id)) })?;
                    let value = self.variant_value(value, b)?;
                    property_value(&mut result, &format!("attribute_{id}"), "TextRange", value);
                }
            }
            Ok(())
        })();
        partial(outcome, b)?;
        result["completeness"] = b.completeness();
        Ok(result)
    }
    pub(super) fn range_text(
        &self,
        range: &IUIAutomationTextRange,
        b: &Budget,
    ) -> Result<Value, EIPError> {
        // The API counts native UTF-16 units. Fetch one extra unit to detect
        // truncation, then independently apply the public UTF-8 byte budget.
        let max = b.limit("max_text_bytes");
        let raw = b.call(|| unsafe { range.GetText((max + 1) as i32) })?;
        let full = raw.to_string();
        if raw.len() > max {
            b.mark("byte_limit");
        }
        let text = b.text(full);
        Ok(
            json!({"status":if text.is_empty(){"empty"}else{"text"},"source":"UIA.TextPattern","text":text}),
        )
    }
    pub(super) fn require_descendant(
        &self,
        child: &IUIAutomationElement,
        parent: &IUIAutomationElement,
        b: &Budget,
    ) -> Result<(), EIPError> {
        let walker = self.walker("raw", b)?;
        let mut current = Some(child.clone());
        for _ in 0..128 {
            let Some(n) = current else {
                break;
            };
            if b.call(|| unsafe { self.automation.CompareElements(&n, parent) })?
                .as_bool()
            {
                return Ok(());
            }
            current = optional(b.call(|| unsafe { walker.GetParentElement(&n) }))?;
        }
        Err(stale())
    }
}
fn endpoint(s: &str) -> Result<TextPatternRangeEndpoint, EIPError> {
    Ok(match s {
        "start" => TextPatternRangeEndpoint_Start,
        "end" => TextPatternRangeEndpoint_End,
        _ => return Err(invalid()),
    })
}
fn text_unit(s: &str) -> Result<TextUnit, EIPError> {
    Ok(match s {
        "character" => TextUnit_Character,
        "format" => TextUnit_Format,
        "word" => TextUnit_Word,
        "line" => TextUnit_Line,
        "paragraph" => TextUnit_Paragraph,
        "page" => TextUnit_Page,
        "document" => TextUnit_Document,
        _ => return Err(unsupported()),
    })
}
fn signed(v: &Value, key: &str) -> Result<i32, EIPError> {
    v[key]
        .as_i64()
        .and_then(|v| i32::try_from(v).ok())
        .ok_or_else(invalid)
}
