//! AX ranges retain their exact owner and native CFRange positions. No byte or
//! scalar offset is substituted for an AX position.
use super::*;
use objc2_core_foundation::{CFAttributedString, CFDictionary};
impl Native {
    pub(super) fn text_length(&self, n: &AXUIElement, b: &Budget) -> Result<isize, EIPError> {
        native::integer(&*get(n, "AXNumberOfCharacters", b)?)
            .and_then(|n| if n >= 0 { Ok(n) } else { Err(unavailable()) })
    }
    pub(super) fn validate_span(
        &self,
        n: &AXUIElement,
        span: CFRange,
        b: &Budget,
    ) -> Result<CFRange, EIPError> {
        validate_span(span, self.text_length(n, b)?)?;
        Ok(span)
    }
    pub(super) fn range_operand(
        &mut self,
        r: &Value,
        owner: &Value,
        b: &Budget,
    ) -> Result<CFRange, EIPError> {
        let n = self.node(owner)?;
        if r["node"] != *owner || r["unit"] != "ax_native" {
            return Err(stale());
        }
        let entry = self
            .handles
            .get_mut(string(r, "range_handle")?)
            .ok_or_else(stale)?;
        entry.touched = Instant::now();
        let span = match &entry.value {
            Handle::Range { owner: o, span } if o == string(owner, "node_handle")? => *span,
            _ => return Err(stale()),
        };
        self.validate_span(&n, span, b)
    }
    pub(super) fn range_reference(
        &mut self,
        owner: &Value,
        span: CFRange,
    ) -> Result<Value, EIPError> {
        let owner_id = string(owner, "node_handle")?.to_owned();
        let id = self.put(
            "ar",
            Handle::Range {
                owner: owner_id,
                span,
            },
        )?;
        Ok(json!({"node":owner,"range_handle":id,"unit":"ax_native"}))
    }
    pub(super) fn selected_spans(
        &self,
        n: &AXUIElement,
        b: &Budget,
    ) -> Result<Vec<CFRange>, EIPError> {
        if let Some(values) = optional(get(n, "AXSelectedTextRanges", b))? {
            let values = native::array(&values)?;
            let mut spans = Vec::new();
            if values.len() > b.limit("max_items").min(256) {
                b.mark("item_limit");
            }
            for i in 0..values.len().min(b.limit("max_items")).min(256) {
                spans.push(self.validate_span(
                    n,
                    native::span(&*values.get(i).ok_or_else(unavailable)?)?,
                    b,
                )?);
            }
            return Ok(spans);
        }
        optional(get(n, "AXSelectedTextRange", b))?
            .map(|v| self.validate_span(n, native::span(&v)?, b))
            .transpose()
            .map(|v| v.into_iter().collect())
    }
    pub(super) fn text_span(
        &self,
        n: &AXUIElement,
        span: CFRange,
        b: &Budget,
    ) -> Result<Value, EIPError> {
        let mut requested = span;
        if requested.length as usize > b.limit("max_text_bytes") {
            requested.length = b.limit("max_text_bytes") as isize;
            b.mark("byte_limit");
        }
        let value = native::parameter(n, "AXStringForRange", &*native::span_value(requested)?, b)?;
        let (text, consumed) = native::text(&value, b)?;
        Ok(
            json!({"status":if text.is_empty(){"empty"}else{"text"},"source":"AXStringForRange","text":text,"span":{"unit":"ax_native","start":span.location,"end":span.location+consumed as isize}}),
        )
    }
    pub(super) fn range(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let owner = &p["node"];
        let n = self.node(owner)?;
        let q = &p["query"];
        let mut out = range_result(owner);
        if self.protected(&n, b)? {
            out["text"] = json!({"status":"protected","source":"AX"});
            return Ok(out);
        }
        let result = (|| {
            let kind = string(q, "kind")?;
            let base = if !p["range"].is_null() {
                Some(self.range_operand(&p["range"], owner, b)?)
            } else {
                None
            };
            let mut spans = Vec::new();
            match kind {
                "document" => spans.push(CFRange {
                    location: 0,
                    length: self.text_length(&n, b)?,
                }),
                "span" => {
                    let s = &q["span"];
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
                    spans.push(self.validate_span(
                        &n,
                        CFRange {
                            location: start,
                            length: end.checked_sub(start).ok_or_else(invalid)?,
                        },
                        b,
                    )?);
                }
                "selection" | "caret" => {
                    spans = self.selected_spans(&n, b)?;
                    if kind == "caret" {
                        spans.retain(|s| s.length == 0);
                    }
                }
                "visible" | "active_composition" => {
                    let key = if kind == "visible" {
                        "AXVisibleCharacterRange"
                    } else {
                        "AXTextInputMarkedRange"
                    };
                    spans.push(self.validate_span(&n, native::span(&*get(&n, key, b)?)?, b)?);
                }
                "index" | "line" | "line_for_index" => {
                    let index = q["index"]
                        .as_u64()
                        .and_then(|v| isize::try_from(v).ok())
                        .ok_or_else(invalid)?;
                    let key = match kind {
                        "index" => "AXRangeForIndex",
                        "line" => "AXRangeForLine",
                        _ => "AXLineForIndex",
                    };
                    let v = native::parameter(&n, key, &CFNumber::new_isize(index), b)?;
                    if kind == "line_for_index" {
                        out["native_index"] = json!(native::integer(&v)?);
                    } else {
                        spans.push(self.validate_span(&n, native::span(&v)?, b)?);
                    }
                }
                "point" => {
                    let point = native_point(&q["point"])?;
                    let v = native::parameter(
                        &n,
                        "AXRangeForPosition",
                        &*native::point_value(point)?,
                        b,
                    )?;
                    spans.push(self.validate_span(&n, native::span(&v)?, b)?);
                }
                "endpoint_by_range" => {
                    let mut s = base.ok_or_else(invalid)?;
                    let other = self.range_operand(&q["other"], owner, b)?;
                    let value = endpoint(other, string(q, "other_endpoint")?)?;
                    let start = if q["endpoint"] == "start" {
                        value
                    } else {
                        s.location
                    };
                    let end = if q["endpoint"] == "end" {
                        value
                    } else {
                        s.location + s.length
                    };
                    // Native range endpoint composition collapses a crossed
                    // opposite endpoint at the new endpoint.
                    s = if q["endpoint"] == "start" {
                        CFRange {
                            location: start,
                            length: (end - start).max(0),
                        }
                    } else {
                        CFRange {
                            location: start.min(end),
                            length: (end - start).max(0),
                        }
                    };
                    spans.push(self.validate_span(&n, s, b)?);
                }
                "expand" => {
                    let s = base.ok_or_else(invalid)?;
                    match q["unit"].as_str() {
                        Some("document") => spans.push(CFRange {
                            location: 0,
                            length: self.text_length(&n, b)?,
                        }),
                        Some("line") => {
                            let line = native::parameter(
                                &n,
                                "AXLineForIndex",
                                &CFNumber::new_isize(s.location),
                                b,
                            )?;
                            let v = native::parameter(&n, "AXRangeForLine", &line, b)?;
                            spans.push(self.validate_span(&n, native::span(&v)?, b)?);
                        }
                        Some("format") => {
                            let v = native::parameter(
                                &n,
                                "AXStyleRangeForIndex",
                                &CFNumber::new_isize(s.location),
                                b,
                            )?;
                            spans.push(self.validate_span(&n, native::span(&v)?, b)?);
                        }
                        _ => return Err(unsupported()),
                    }
                }
                "find_text" => {
                    let mut s = base.unwrap_or(CFRange {
                        location: 0,
                        length: self.text_length(&n, b)?,
                    });
                    let max = b.limit("max_text_bytes") as isize;
                    if s.length > max {
                        if q["backward"] == true {
                            s.location += s.length - max;
                        }
                        s.length = max;
                        b.mark("byte_limit");
                    }
                    let v = self.text_span(&n, s, b)?;
                    let text = string(&v, "text")?;
                    let needle = string(q, "text")?;
                    if needle.is_empty() {
                        return Err(invalid());
                    }
                    if q["ignore_case"] == true {
                        return Err(unsupported());
                    }
                    let found = if q["backward"] == true {
                        text.rfind(needle)
                    } else {
                        text.find(needle)
                    };
                    if let Some(index) = found {
                        spans.push(CFRange {
                            location: s.location + text[..index].encode_utf16().count() as isize,
                            length: needle.encode_utf16().count() as isize,
                        });
                    }
                }
                // AX has no generic UIA movement, attribute-ID search, conversion
                // target or range-from-child operation. Keep this distinction.
                _ => return Err(unsupported()),
            }
            for s in spans.into_iter().take(b.limit("max_items").min(256)) {
                out["ranges"]
                    .as_array_mut()
                    .unwrap()
                    .push(self.range_reference(owner, s)?);
            }
            Ok(())
        })();
        partial(result, b)?;
        out["completeness"] = b.completeness();
        Ok(out)
    }
    pub(super) fn range_read(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let owner = &p["range"]["node"];
        let n = self.node(owner)?;
        let mut out = range_result(owner);
        if self.protected(&n, b)? {
            out["text"] = json!({"status":"protected","source":"AX"});
            return Ok(out);
        }
        let result = (|| {
            let span = self.range_operand(&p["range"], owner, b)?;
            let r = &p["request"];
            match string(r, "kind")? {
                "text" => out["text"] = self.text_span(&n, span, b)?,
                "bounds" => {
                    let value =
                        native::parameter(&n, "AXBoundsForRange", &*native::span_value(span)?, b)?;
                    out["bounds"]
                        .as_array_mut()
                        .unwrap()
                        .push(rect_value(native::rectangle(&value)?)?);
                }
                "enclosing_element" => out["nodes"]
                    .as_array_mut()
                    .unwrap()
                    .push(self.sample(&n, b)?),
                "compare" => {
                    let other = self.range_operand(&r["other"], owner, b)?;
                    out["comparison"] = if r["endpoint"].is_null() {
                        json!(span == other)
                    } else {
                        json!(
                            endpoint(span, string(r, "endpoint")?)?
                                - endpoint(other, string(r, "other_endpoint")?)?
                        )
                    };
                }
                "attributes" => {
                    if r["attribute_ids"].as_array().is_some_and(|v| !v.is_empty()) {
                        return Err(unsupported());
                    }
                    let mut part = span;
                    if part.length as usize > b.limit("max_text_bytes") {
                        part.length = b.limit("max_text_bytes") as isize;
                        b.mark("byte_limit");
                    }
                    let attributed = typed::<CFAttributedString>(native::parameter(
                        &n,
                        "AXAttributedStringForRange",
                        &*native::span_value(part)?,
                        b,
                    )?)?;
                    let length = attributed.length();
                    let mut offset = 0;
                    while offset < length {
                        b.check()?;
                        if out["attribute_runs"].as_array().unwrap().len() >= b.limit("max_items") {
                            b.mark("item_limit");
                            break;
                        }
                        let mut effective = CFRange {
                            location: 0,
                            length: 0,
                        };
                        let attributes = unsafe { attributed.attributes(offset, &mut effective) }
                            .ok_or_else(unavailable)?;
                        let end = effective
                            .location
                            .checked_add(effective.length)
                            .filter(|end| *end > offset && *end <= length)
                            .ok_or_else(unavailable)?;
                        let properties = self.attribute_properties(&attributes, b)?;
                        let reference = self.range_reference(
                            owner,
                            CFRange {
                                location: part.location + offset,
                                length: end - offset,
                            },
                        )?;
                        out["attribute_runs"]
                            .as_array_mut()
                            .unwrap()
                            .push(json!({"range":reference,"properties":properties}));
                        offset = end;
                    }
                }
                _ => return Err(unsupported()),
            }
            Ok(())
        })();
        partial(result, b)?;
        out["completeness"] = b.completeness();
        Ok(out)
    }
    fn attribute_properties(
        &mut self,
        dict: &CFDictionary,
        b: &Budget,
    ) -> Result<Vec<Value>, EIPError> {
        let len = dict.len();
        if len > b.limit("max_items").min(128) {
            b.mark("item_limit");
            return Ok(vec![]);
        }
        let mut keys = vec![std::ptr::null(); len];
        let mut values = vec![std::ptr::null(); len];
        unsafe {
            dict.keys_and_values(keys.as_mut_ptr(), values.as_mut_ptr());
        }
        let mut properties = Vec::new();
        for (key, value) in keys.into_iter().zip(values) {
            if key.is_null() || value.is_null() {
                return Err(unavailable());
            }
            // Get pointers stay borrowed from the retained dictionary, and
            // conversion retains any node exported through the registry.
            let key = unsafe { &*key.cast::<CFType>() };
            let value = unsafe { &*value.cast::<CFType>() };
            let name = native::name(key, b)?;
            if name.len() > 128 {
                continue;
            }
            properties.push(json!({"name":name,"source":"AXAttributedStringForRange","value":self.value(value,b)?}));
        }
        Ok(properties)
    }
}
pub(super) fn validate_span(s: CFRange, length: isize) -> Result<(), EIPError> {
    if s.location < 0
        || s.length < 0
        || s.location
            .checked_add(s.length)
            .is_none_or(|end| end > length)
    {
        return Err(invalid());
    }
    Ok(())
}
fn endpoint(s: CFRange, end: &str) -> Result<isize, EIPError> {
    match end {
        "start" => Ok(s.location),
        "end" => s.location.checked_add(s.length).ok_or_else(invalid),
        _ => Err(invalid()),
    }
}
fn range_result(owner: &Value) -> Value {
    json!({"node":owner,"sampled_at":now(),"ranges":[],"nodes":[],"bounds":[],"properties":[],"attribute_runs":[],"field_errors":[],"completeness":{"complete":true}})
}
