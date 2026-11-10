//! Character-based AT-SPI spans. Handles retain owners, never UTF-8 offsets.
use super::*;
use atspi::common::{ClipType, Granularity};
impl Native {
    pub(super) fn span(&mut self, r: &Value) -> Result<(ObjectRefOwned, i32, i32), EIPError> {
        let owner = self.node(&r["node"])?;
        if r["unit"] != "atspi_character" {
            return Err(stale());
        }
        match self.registry.get(string(r, "range_handle")?)? {
            Handle::Range { node, start, end } if node == owner => Ok((node, start, end)),
            _ => Err(stale()),
        }
    }
    pub(super) async fn valid_span(
        &self,
        node: &ObjectRefOwned,
        start: i32,
        end: i32,
        b: &Budget,
    ) -> Result<i32, EIPError> {
        self.require(node, Interface::Text, b).await?;
        let count = b.call(self.text(node).await?.character_count()).await?;
        if start < 0 || end < start || end > count {
            return Err(error(
                ErrorType::InvalidParams,
                "text span outside current native character bounds",
            ));
        }
        Ok(count)
    }
    pub(super) fn range_ref(
        &mut self,
        node: &ObjectRefOwned,
        start: i32,
        end: i32,
    ) -> Result<Value, EIPError> {
        let reference = self.reference(node.clone())?;
        let id = self.registry.put(
            "ar",
            Handle::Range {
                node: node.clone(),
                start,
                end,
            },
        )?;
        Ok(json!({"node":reference,"range_handle":id,"unit":"atspi_character"}))
    }
    pub(super) async fn text_content(
        &self,
        node: &ObjectRefOwned,
        start: i32,
        end: i32,
        b: &Budget,
    ) -> Result<Value, EIPError> {
        if self.protected(node, b).await? {
            return Ok(json!({"status":"protected","source":"atspi.Text"}));
        }
        self.valid_span(node, start, end, b).await?;
        // At most max_text_bytes characters are fetched; UTF-8 is bounded again
        // independently. No -1/full-document request defeats the text budget.
        let fetch_end = end.min(start.saturating_add(b.limit("max_text_bytes") as i32));
        if fetch_end < end {
            b.mark("byte_limit");
        }
        let text = b.text(
            b.call(self.text(node).await?.get_text(start, fetch_end))
                .await?,
        );
        let actual_end = start + text.chars().count() as i32;
        Ok(
            json!({"status":if text.is_empty(){"empty"}else{"text"},"source":"atspi.Text","text":text,
            "span":{"unit":"atspi_character","start":start,"end":actual_end}}),
        )
    }
    pub(super) async fn range(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let node = self.node(&p["node"])?;
        let mut result = json!({"node":p["node"],"sampled_at":now(),"completeness":b.completeness(),"ranges":[],"bounds":[],"properties":[],"attribute_runs":[],"nodes":[],"field_errors":[]});
        let mut spans = Vec::new();
        let read = self.range_into(&node, p, b, &mut result, &mut spans).await;
        if let Err(err) = read {
            if !matches!(b.reason.get(), Some("call_limit" | "deadline")) {
                return Err(err);
            }
            field_error(&mut result, "ranges", &err);
        }
        for (start, end) in spans {
            result["ranges"]
                .as_array_mut()
                .unwrap()
                .push(self.range_ref(&node, start, end)?);
        }
        Ok(result)
    }
    async fn range_into(
        &mut self,
        node: &ObjectRefOwned,
        p: &Value,
        b: &Budget,
        result: &mut Value,
        spans: &mut Vec<(i32, i32)>,
    ) -> Result<(), EIPError> {
        self.require(node, Interface::Text, b).await?;
        if self.protected(node, b).await? {
            result["text"] = json!({"status":"protected","source":"atspi.Text"});
            return Ok(());
        }
        let text = self.text(node).await?;
        let count = b.call(text.character_count()).await?;
        if count < 0 {
            return Err(stale());
        }
        let source = if p["source"].is_null() {
            (0, count)
        } else {
            let (owner, start, end) = self.span(&p["source"])?;
            if &owner != node {
                return Err(stale());
            }
            self.valid_span(node, start, end, b).await?;
            (start, end)
        };
        let q = &p["query"];
        let kind = string(q, "kind")?;
        match kind {
            "document" => spans.push((0, count)),
            "span" => {
                if q["span"]["unit"] != "atspi_character" {
                    return Err(error(
                        ErrorType::InvalidParams,
                        "AT-SPI character span required",
                    ));
                }
                let start = integer(&q["span"], "start")?;
                let end = integer(&q["span"], "end")?;
                self.valid_span(node, start, end, b).await?;
                spans.push((start, end));
            }
            "caret" => {
                let offset = b.call(text.caret_offset()).await?;
                self.valid_span(node, offset, offset, b).await?;
                spans.push((offset, offset));
            }
            "selection" => {
                let n = b.call(text.get_n_selections()).await?.max(0) as usize;
                if n > b.limit("max_items").min(256) {
                    b.mark("item_limit");
                }
                for i in 0..n.min(b.limit("max_items")).min(256) {
                    if b.check().is_err() {
                        break;
                    }
                    let span = b.call(text.get_selection(i as i32)).await?;
                    self.valid_span(node, span.0, span.1, b).await?;
                    spans.push(span);
                }
            }
            "point" => {
                let (frame, x, y) = point(&q["point"])?;
                let offset = b.call(text.get_offset_at_point(x, y, frame)).await?;
                self.valid_span(node, offset, offset, b).await?;
                spans.push((offset, offset));
            }
            "visible" => {
                let (x, y, w, h) = b
                    .call(self.component(node).await?.get_extents(CoordType::Screen))
                    .await?;
                let ranges = b
                    .call(text.get_bounded_ranges(
                        x,
                        y,
                        w,
                        h,
                        CoordType::Screen,
                        ClipType::Neither,
                        ClipType::Neither,
                    ))
                    .await?;
                if ranges.len() > b.limit("max_items").min(256) {
                    b.mark("item_limit");
                }
                for (start, end, _, _) in ranges.into_iter().take(b.limit("max_items").min(256)) {
                    self.valid_span(node, start, end, b).await?;
                    spans.push((start, end));
                }
            }
            "index" => {
                let index = integer(q, "index")?;
                self.valid_span(node, index, index, b).await?;
                spans.push((index, index));
            }
            "line" | "line_for_index" => {
                let index = integer(q, "index")?;
                let mut offset = 0;
                let mut line = 0;
                while offset < count {
                    if b.check().is_err() {
                        break;
                    }
                    let (_, start, end) = b
                        .call(text.get_string_at_offset(offset, Granularity::Line))
                        .await?;
                    if end <= offset || start < 0 || end > count {
                        return Err(stale());
                    }
                    if (kind == "line" && line == index)
                        || (kind == "line_for_index" && start <= index && index < end)
                    {
                        spans.push((start, end));
                        result["native_index"] = json!(line);
                        break;
                    }
                    offset = end;
                    line += 1;
                }
            }
            "find_text" => {
                let backward = q["backward"] == true;
                let (search_start, content) = if backward {
                    let start = source
                        .0
                        .max(source.1.saturating_sub(b.limit("max_text_bytes") as i32));
                    if start > source.0 {
                        b.mark("byte_limit");
                    }
                    let text = b.call(text.get_text(start, source.1)).await?;
                    let mut offset = text.len().saturating_sub(b.limit("max_text_bytes"));
                    while !text.is_char_boundary(offset) {
                        offset += 1;
                    }
                    if offset > 0 {
                        b.mark("byte_limit");
                    }
                    let suffix = text[offset..].to_owned();
                    (source.1 - suffix.chars().count() as i32, suffix)
                } else {
                    let content = self.text_content(node, source.0, source.1, b).await?;
                    (source.0, content["text"].as_str().unwrap_or("").to_owned())
                };
                let haystack: Vec<char> = content.chars().collect();
                let needle = string(q, "text")?;
                if needle.is_empty() {
                    return Err(error(
                        ErrorType::InvalidParams,
                        "nonempty search text required",
                    ));
                }
                let ignore = q["ignore_case"] == true;
                let expected = if ignore {
                    needle.case_fold().collect::<String>()
                } else {
                    needle.to_owned()
                };
                let starts: Box<dyn Iterator<Item = usize>> = if q["backward"] == true {
                    Box::new((0..haystack.len()).rev())
                } else {
                    Box::new(0..haystack.len())
                };
                'search: for start in starts {
                    if b.check().is_err() {
                        break;
                    }
                    // Compare native character slices so case expansion never
                    // turns folded byte indices into native offsets.
                    let mut candidate = String::new();
                    for (end, ch) in haystack.iter().enumerate().skip(start) {
                        candidate.push(*ch);
                        let actual = if ignore {
                            candidate.case_fold().collect::<String>()
                        } else {
                            candidate.clone()
                        };
                        if actual == expected {
                            spans
                                .push((search_start + start as i32, search_start + end as i32 + 1));
                            break 'search;
                        }
                        if !expected.starts_with(&actual) {
                            break;
                        }
                    }
                }
            }
            "endpoint_by_range" => {
                let (owner, start, end) = self.span(&q["other"])?;
                if &owner != node {
                    return Err(stale());
                }
                let value = if q["other_endpoint"] == "start" {
                    start
                } else {
                    end
                };
                let mut span = source;
                if q["endpoint"] == "start" {
                    span.0 = value;
                    span.1 = span.1.max(value);
                } else {
                    span.1 = value;
                    span.0 = span.0.min(value);
                }
                self.valid_span(node, span.0, span.1, b).await?;
                spans.push(span);
            }
            "expand" | "move" | "move_endpoint" => {
                let unit = string(q, "unit")?;
                let mut span = source;
                let mut actual = 0;
                if kind == "expand" {
                    if unit == "document" {
                        span = (0, count);
                    } else if count > 0 {
                        let (_, start, end) = b
                            .call(
                                text.get_string_at_offset(
                                    span.0.min(count - 1),
                                    granularity(unit)?,
                                ),
                            )
                            .await?;
                        let end_at = (span.1 - 1).max(span.0).min(count - 1);
                        let (_, end_start, end_end) = b
                            .call(text.get_string_at_offset(end_at, granularity(unit)?))
                            .await?;
                        span = (
                            start.min(end_start).min(span.0),
                            end.max(end_end).max(span.1),
                        );
                    }
                } else {
                    let count_move = integer(q, "count")?;
                    let endpoint = q["endpoint"].as_str();
                    let mut offset = if endpoint == Some("end") {
                        span.1
                    } else {
                        span.0
                    };
                    for _ in 0..count_move.unsigned_abs() {
                        if b.check().is_err() {
                            break;
                        }
                        let previous = offset;
                        offset = if unit == "character" {
                            (offset + count_move.signum()).clamp(0, count)
                        } else if unit == "document" {
                            if count_move > 0 { count } else { 0 }
                        } else if count == 0 {
                            0
                        } else {
                            let at = if count_move < 0 {
                                (offset - 1).max(0)
                            } else {
                                offset.min(count - 1)
                            };
                            let (_, start, end) = b
                                .call(text.get_string_at_offset(at, granularity(unit)?))
                                .await?;
                            if count_move < 0 { start } else { end }
                        };
                        if offset == previous {
                            break;
                        }
                        actual += count_move.signum();
                    }
                    if kind == "move_endpoint" || endpoint.is_some() {
                        if endpoint == Some("end") {
                            span.1 = offset;
                            span.0 = span.0.min(offset);
                        } else {
                            span.0 = offset;
                            span.1 = span.1.max(offset);
                        }
                    } else {
                        let length = span.1 - span.0;
                        span = (offset, (offset + length).min(count));
                    }
                }
                self.valid_span(node, span.0, span.1, b).await?;
                spans.push(span);
                result["actual_movement"] = json!(actual);
            }
            _ => {
                return Err(error(
                    ErrorType::Unsupported,
                    "range query has no AT-SPI native equivalent",
                ));
            }
        }
        Ok(())
    }
    pub(super) async fn range_read(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let (node, start, end) = self.span(&p["reference"])?;
        let mut result = json!({"node":p["reference"]["node"],"sampled_at":now(),"completeness":b.completeness(),"ranges":[],"bounds":[],"properties":[],"attribute_runs":[],"nodes":[],"field_errors":[]});
        let read = self
            .range_read_into(&node, start, end, p, b, &mut result)
            .await;
        if let Err(err) = read {
            if !matches!(b.reason.get(), Some("call_limit" | "deadline")) {
                return Err(err);
            }
            field_error(&mut result, "range", &err);
        }
        Ok(result)
    }
    async fn range_read_into(
        &mut self,
        node: &ObjectRefOwned,
        start: i32,
        end: i32,
        p: &Value,
        b: &Budget,
        result: &mut Value,
    ) -> Result<(), EIPError> {
        self.valid_span(node, start, end, b).await?;
        if self.protected(node, b).await? {
            result["text"] = json!({"status":"protected","source":"atspi.Text"});
            return Ok(());
        }
        let text = self.text(node).await?;
        match p["request"]["kind"].as_str() {
            Some("text") => result["text"] = self.text_content(node, start, end, b).await?,
            Some("bounds") => {
                let rect = b
                    .call(text.get_range_extents(start, end, CoordType::Screen))
                    .await?;
                result["bounds"] = json!([bounds(rect, "atspi_screen")]);
            }
            Some("attributes") => {
                let mut offset = start;
                while offset < end {
                    if result["attribute_runs"].as_array().unwrap().len() >= b.limit("max_items") {
                        b.mark("item_limit");
                        break;
                    }
                    if b.check().is_err() {
                        break;
                    }
                    let (attrs, from, to) = b.call(text.get_attribute_run(offset, true)).await?;
                    if from > offset || to <= offset {
                        return Err(stale());
                    }
                    let properties = read::string_properties(attrs, "atspi.Text", b);
                    result["attribute_runs"].as_array_mut().unwrap().push(json!({"range":self.range_ref(node,from.max(start),to.min(end))?,"properties":properties}));
                    offset = to;
                }
            }
            Some("enclosing_element") => result["nodes"] = json!([self.sample(node, b).await?]),
            Some("compare") => {
                let (owner, other_start, other_end) = self.span(&p["request"]["other"])?;
                if &owner != node {
                    return Err(stale());
                }
                self.valid_span(node, other_start, other_end, b).await?;
                result["comparison"] = if p["request"]["endpoint"].is_null() {
                    json!(start == other_start && end == other_end)
                } else {
                    let a = if p["request"]["endpoint"] == "start" {
                        start
                    } else {
                        end
                    };
                    let other = if p["request"]["other_endpoint"] == "start" {
                        other_start
                    } else {
                        other_end
                    };
                    json!(a - other)
                };
            }
            _ => {
                return Err(error(
                    ErrorType::Unsupported,
                    "range read has no AT-SPI native equivalent",
                ));
            }
        }
        Ok(())
    }
}
fn granularity(unit: &str) -> Result<Granularity, EIPError> {
    match unit {
        "character" => Ok(Granularity::Char),
        "word" => Ok(Granularity::Word),
        "line" => Ok(Granularity::Line),
        "paragraph" => Ok(Granularity::Paragraph),
        _ => Err(error(
            ErrorType::Unsupported,
            "native text granularity is unsupported",
        )),
    }
}
