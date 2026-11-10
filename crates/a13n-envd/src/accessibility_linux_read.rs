//! Typed observations of AT-SPI interfaces, including cross-object selections.
use super::*;
impl Native {
    pub(super) async fn read(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let node = self.node(&p["node"])?;
        let r = &p["request"];
        let kind = string(r, "kind")?;
        let mut out = json!({"node":p["node"],"sampled_at":now(),"completeness":b.completeness(),"properties":[],"nodes":[],"ranges":[],"field_errors":[],"document_selections":[],"attributes":[]});
        if let Err(err) = self.live(&node, b).await {
            if b.check().is_ok() {
                return Err(err);
            }
            field_error(&mut out, "node", &err);
            return Ok(out);
        }
        let protected = match self.protected(&node, b).await {
            Ok(protected) => protected,
            Err(err) => {
                field_error(&mut out, "protection", &err);
                return Ok(out);
            }
        };
        if protected {
            out["text"] = json!({"status":"protected","source":"atspi.Text"});
            out["field_errors"] = json!([{"field":kind,"reason":"protected"}]);
            return Ok(out);
        }
        let result = self.read_into(&node, r, b, &mut out).await;
        if let Err(err) = result {
            field_error(&mut out, kind, &err);
        }
        Ok(out)
    }
    async fn read_into(
        &mut self,
        node: &ObjectRefOwned,
        r: &Value,
        b: &Budget,
        out: &mut Value,
    ) -> Result<(), EIPError> {
        match string(r, "kind")? {
            "text" => {
                self.require(node, Interface::Text, b).await?;
                let count = b.call(self.text(node).await?.character_count()).await?;
                let (start, end) = if r["span"].is_null() {
                    (0, count)
                } else {
                    if r["span"]["unit"] != "atspi_character" {
                        return Err(error(
                            ErrorType::InvalidParams,
                            "AT-SPI character span required",
                        ));
                    }
                    (integer(&r["span"], "start")?, integer(&r["span"], "end")?)
                };
                out["text"] = self.text_content(node, start, end, b).await?;
                property(out, "character_count", "atspi.Text", json!(count), b);
                if let Ok(caret) = b.call(self.text(node).await?.caret_offset()).await {
                    property(out, "caret_offset", "atspi.Text", json!(caret), b);
                }
            }
            "properties" => {
                let sample = self.sample(node, b).await?;
                for group in r["groups"].as_array().into_iter().flatten() {
                    match group.as_str() {
                        Some("identity") => {
                            for name in ["native_role", "name", "description", "identifier"] {
                                if !sample[name].is_null() {
                                    property(
                                        out,
                                        name,
                                        "atspi.Accessible",
                                        sample[name].clone(),
                                        b,
                                    );
                                }
                            }
                        }
                        Some("state") => {
                            if let Some(states) = sample["states"].as_object() {
                                for (name, value) in states {
                                    property(out, name, "atspi.Accessible", value.clone(), b);
                                }
                            }
                        }
                        Some("value") => {
                            self.numeric(node, b, out).await?;
                        }
                        Some("window") => {
                            self.component_details(node, b, out).await?;
                        }
                        _ => {}
                    }
                }
                let p = self.accessible(node).await?;
                let attrs = b.call(p.get_attributes()).await?;
                append_properties(out, string_properties(attrs, "atspi.Accessible", b), b);
                if let Ok(locale) = b.call(p.locale()).await {
                    property(out, "locale", "atspi.Accessible", json!(b.text(locale)), b);
                }
            }
            "numeric_value" => self.numeric(node, b, out).await?,
            "navigate" => {
                let p = self.accessible(node).await?;
                let direction = string(r, "direction")?;
                let target = match direction {
                    "parent" => self.parent(node, b).await?,
                    "first_child" => b.call(p.get_child_at_index(0)).await?,
                    "last_child" => {
                        let count = b.call(p.child_count()).await?;
                        if count == 0 {
                            return Ok(());
                        }
                        b.call(p.get_child_at_index(count - 1)).await?
                    }
                    "next_sibling" | "previous_sibling" => {
                        let parent = self.parent(node, b).await?;
                        if parent.is_null() {
                            return Ok(());
                        }
                        let index = b.call(p.get_index_in_parent()).await?
                            + if direction == "next_sibling" { 1 } else { -1 };
                        if index < 0 {
                            return Ok(());
                        }
                        let parent = self.accessible(&parent).await?;
                        if index >= b.call(parent.child_count()).await? {
                            return Ok(());
                        }
                        b.call(parent.get_child_at_index(index)).await?
                    }
                    _ => return Err(error(ErrorType::InvalidParams, "invalid native navigation")),
                };
                if !target.is_null() {
                    out["nodes"] = json!([self.sample(&target, b).await?]);
                }
            }
            "relations" => {
                let relations = b
                    .call(self.accessible(node).await?.get_relation_set())
                    .await?;
                for (kind, targets) in relations {
                    let name = format!("{kind:?}");
                    if let Some(filter) = r["relations"].as_array()
                        && !filter.is_empty()
                        && !filter.iter().any(|v| v.as_str() == Some(&name))
                    {
                        continue;
                    }
                    if targets.len() > b.limit("max_items") {
                        b.mark("item_limit");
                    }
                    let mut refs = Vec::new();
                    for target in targets.into_iter().take(b.limit("max_items")) {
                        if !target.is_null() {
                            refs.push(self.reference(target)?);
                        }
                    }
                    value_property(
                        out,
                        &name,
                        "atspi.Accessible",
                        json!({"kind":"nodes","values":refs}),
                        b,
                    );
                }
            }
            "selection" => {
                let interfaces = b
                    .call(self.accessible(node).await?.get_interfaces())
                    .await?;
                if interfaces.contains(Interface::Selection) {
                    let proxy = self.selection(node).await?;
                    let count = b.call(proxy.n_selected_children()).await?.max(0) as usize;
                    property(out, "selected_count", "atspi.Selection", json!(count), b);
                    if count > b.limit("max_items") {
                        b.mark("item_limit");
                    }
                    for i in 0..count.min(b.limit("max_items")) {
                        if b.check().is_err() {
                            break;
                        }
                        let child = b.call(proxy.get_selected_child(i as i32)).await?;
                        let sample = self.sample(&child, b).await?;
                        out["nodes"].as_array_mut().unwrap().push(sample);
                    }
                }
                if interfaces.contains(Interface::Text) {
                    let proxy = self.text(node).await?;
                    let count = b.call(proxy.get_n_selections()).await?.max(0) as usize;
                    if count > b.limit("max_items").min(256) {
                        b.mark("item_limit");
                    }
                    for i in 0..count.min(b.limit("max_items")).min(256) {
                        if b.check().is_err() {
                            break;
                        }
                        let (start, end) = b.call(proxy.get_selection(i as i32)).await?;
                        self.valid_span(node, start, end, b).await?;
                        let reference = self.range_ref(node, start, end)?;
                        out["ranges"].as_array_mut().unwrap().push(reference);
                    }
                    property(
                        out,
                        "caret_offset",
                        "atspi.Text",
                        json!(b.call(proxy.caret_offset()).await?),
                        b,
                    );
                }
                if !interfaces.contains(Interface::Selection)
                    && !interfaces.contains(Interface::Text)
                {
                    return Err(error(
                        ErrorType::Unsupported,
                        "no native selection interface",
                    ));
                }
            }
            "document" => {
                self.require(node, Interface::Document, b).await?;
                let proxy = self.document(node).await?;
                property(
                    out,
                    "locale",
                    "atspi.Document",
                    json!(b.text(b.call(proxy.get_locale()).await?)),
                    b,
                );
                property(
                    out,
                    "page_count",
                    "atspi.Document",
                    json!(b.call(proxy.page_count()).await?),
                    b,
                );
                property(
                    out,
                    "current_page",
                    "atspi.Document",
                    json!(b.call(proxy.current_page_number()).await?),
                    b,
                );
                append_properties(
                    out,
                    string_properties(b.call(proxy.get_attributes()).await?, "atspi.Document", b),
                    b,
                );
            }
            "document_selections" => {
                self.require(node, Interface::Document, b).await?;
                let proxy = self.document_selections_proxy(node).await?;
                let selections = b.call(proxy.get_text_selections()).await?;
                if selections.len() > b.limit("max_items").min(256) {
                    b.mark("item_limit");
                }
                for (start, from, end, to, active) in
                    selections.into_iter().take(b.limit("max_items").min(256))
                {
                    let start = ObjectRef::try_from_bus_name_and_path(
                        start.0.as_str().try_into().map_err(|_| stale())?,
                        start.1.as_ref(),
                    )
                    .map_err(|_| stale())?
                    .into_owned();
                    let end = ObjectRef::try_from_bus_name_and_path(
                        end.0.as_str().try_into().map_err(|_| stale())?,
                        end.1.as_ref(),
                    )
                    .map_err(|_| stale())?
                    .into_owned();
                    let start = ObjectRefOwned::new(start);
                    let end = ObjectRefOwned::new(end);
                    if from < 0
                        || to < 0
                        || !self.descendant(&start, node, b).await?
                        || !self.descendant(&end, node, b).await?
                    {
                        return Err(stale());
                    }
                    if self.protected(&start, b).await? || self.protected(&end, b).await? {
                        out["field_errors"]
                            .as_array_mut()
                            .expect("field errors")
                            .push(json!({"field":"document_selections","reason":"protected"}));
                        continue;
                    }
                    self.valid_span(&start, from, from, b).await?;
                    self.valid_span(&end, to, to, b).await?;
                    out["document_selections"].as_array_mut().unwrap().push(json!({"start":self.reference(start)?,"start_offset":from,"end":self.reference(end)?,"end_offset":to,"start_is_active":active}));
                }
            }
            "table" => self.table_read(node, r, b, out).await?,
            "native_details" => match r["group"].as_str() {
                Some("atspi_interfaces") => {
                    let interfaces = b
                        .call(self.accessible(node).await?.get_interfaces())
                        .await?;
                    for interface in interfaces.iter() {
                        property(
                            out,
                            &format!("{interface:?}"),
                            "atspi.Accessible",
                            json!(true),
                            b,
                        );
                    }
                }
                Some("atspi_component") => self.component_details(node, b, out).await?,
                Some("atspi_actions") => {
                    self.require(node, Interface::Action, b).await?;
                    let actions = self.actions(node, b).await?;
                    for (i, (name, label, description, key)) in actions.iter().cloned().enumerate()
                    {
                        for (suffix, value) in [
                            ("name", name),
                            ("localized_name", label),
                            ("description", description),
                            ("key_binding", key),
                        ] {
                            property(
                                out,
                                &format!("action.{i}.{suffix}"),
                                "atspi.Action",
                                json!(b.text(value)),
                                b,
                            );
                        }
                    }
                    out["nodes"] = json!([self.sample(node, b).await?]);
                }
                Some("atspi_image") => {
                    self.require(node, Interface::Image, b).await?;
                    let proxy = self.image(node).await?;
                    property(
                        out,
                        "description",
                        "atspi.Image",
                        json!(b.text(b.call(proxy.image_description()).await?)),
                        b,
                    );
                    property(
                        out,
                        "locale",
                        "atspi.Image",
                        json!(b.text(b.call(proxy.image_locale()).await?)),
                        b,
                    );
                    // Image screen coordinates are deprecated by the native
                    // protocol; retain explicitly window-relative geometry.
                    let rect = b.call(proxy.get_image_extents(CoordType::Window)).await?;
                    value_property(
                        out,
                        "bounds",
                        "atspi.Image",
                        json!({"kind":"bounds","values":[bounds(rect,"atspi_window")]}),
                        b,
                    );
                }
                Some("atspi_hypertext") => self.hypertext_read(node, b, out).await?,
                _ => {
                    return Err(error(
                        ErrorType::Unsupported,
                        "native detail group is not supported by AT-SPI",
                    ));
                }
            },
            _ => {
                return Err(error(
                    ErrorType::Unsupported,
                    "read request has no AT-SPI native equivalent",
                ));
            }
        }
        Ok(())
    }
    async fn numeric(
        &self,
        node: &ObjectRefOwned,
        b: &Budget,
        out: &mut Value,
    ) -> Result<(), EIPError> {
        self.require(node, Interface::Value, b).await?;
        let proxy = self.value(node).await?;
        for (name, value) in [
            ("current", b.call(proxy.current_value()).await),
            ("minimum", b.call(proxy.minimum_value()).await),
            ("maximum", b.call(proxy.maximum_value()).await),
            ("increment", b.call(proxy.minimum_increment()).await),
        ] {
            match value {
                Ok(value) if value.is_finite() => {
                    property(out, name, "atspi.Value", json!(value), b)
                }
                Err(err) => field_error(out, name, &err),
                _ => field_error(out, name, &unavailable()),
            }
        }
        if let Ok(text) = b.call(proxy.text()).await {
            property(out, "text", "atspi.Value", json!(b.text(text)), b);
        }
        Ok(())
    }
    async fn component_details(
        &self,
        node: &ObjectRefOwned,
        b: &Budget,
        out: &mut Value,
    ) -> Result<(), EIPError> {
        self.require(node, Interface::Component, b).await?;
        let proxy = self.component(node).await?;
        for frame in [CoordType::Screen, CoordType::Window, CoordType::Parent] {
            let tag = match frame {
                CoordType::Screen => "atspi_screen",
                CoordType::Window => "atspi_window",
                CoordType::Parent => "atspi_parent",
            };
            match b.call(proxy.get_extents(frame)).await {
                Ok(rect) => value_property(
                    out,
                    tag,
                    "atspi.Component",
                    json!({"kind":"bounds","values":[bounds(rect,tag)]}),
                    b,
                ),
                Err(err) => field_error(out, tag, &err),
            }
        }
        property(
            out,
            "layer",
            "atspi.Component",
            json!(format!("{:?}", b.call(proxy.get_layer()).await?)),
            b,
        );
        property(
            out,
            "mdi_z_order",
            "atspi.Component",
            json!(b.call(proxy.get_mdiz_order()).await?),
            b,
        );
        let alpha = b.call(proxy.get_alpha()).await?;
        if alpha.is_finite() {
            property(out, "alpha", "atspi.Component", json!(alpha), b);
        }
        Ok(())
    }
    async fn table_read(
        &mut self,
        node: &ObjectRefOwned,
        r: &Value,
        b: &Budget,
        out: &mut Value,
    ) -> Result<(), EIPError> {
        let interfaces = b
            .call(self.accessible(node).await?.get_interfaces())
            .await?;
        if interfaces.contains(Interface::TableCell) {
            let cell = self.cell(node).await?;
            let (valid, row, column, rows, columns) = b.call(cell.get_row_column_span()).await?;
            for (name, value) in [
                ("position_valid", json!(valid)),
                ("row", json!(row)),
                ("column", json!(column)),
                ("row_span", json!(rows)),
                ("column_span", json!(columns)),
            ] {
                property(out, name, "atspi.TableCell", value, b);
            }
            let table = b.call(cell.table()).await?;
            if !table.is_null() {
                value_property(
                    out,
                    "table",
                    "atspi.TableCell",
                    json!({"kind":"nodes","values":[self.reference(table)?]}),
                    b,
                );
            }
            for (name, headers) in [
                ("row_headers", b.call(cell.get_row_header_cells()).await),
                (
                    "column_headers",
                    b.call(cell.get_column_header_cells()).await,
                ),
            ] {
                if let Ok(headers) = headers {
                    if headers.len() > b.limit("max_items") {
                        b.mark("item_limit");
                    }
                    let mut refs = Vec::new();
                    for header in headers.into_iter().take(b.limit("max_items")) {
                        refs.push(self.reference(header)?);
                    }
                    value_property(
                        out,
                        name,
                        "atspi.TableCell",
                        json!({"kind":"nodes","values":refs}),
                        b,
                    );
                }
            }
            return Ok(());
        }
        self.require(node, Interface::Table, b).await?;
        let table = self.table(node).await?;
        let rows = b.call(table.n_rows()).await?;
        let columns = b.call(table.n_columns()).await?;
        property(out, "rows", "atspi.Table", json!(rows), b);
        property(out, "columns", "atspi.Table", json!(columns), b);
        let first_row = integer(r, "row")?;
        let first_column = integer(r, "column")?;
        if first_row < 0 || first_column < 0 || first_row > rows || first_column > columns {
            return Err(error(
                ErrorType::InvalidParams,
                "table region outside native bounds",
            ));
        }
        let end_row = first_row.saturating_add(integer(r, "rows")?).min(rows);
        let end_column = first_column
            .saturating_add(integer(r, "columns")?)
            .min(columns);

        'cells: for row in first_row..end_row {
            for column in first_column..end_column {
                if out["nodes"].as_array().unwrap().len() >= b.limit("max_items") {
                    b.mark("item_limit");
                    break 'cells;
                }
                if b.check().is_err() {
                    break 'cells;
                }
                let cell = b.call(table.get_accessible_at(row, column)).await?;
                if cell.is_null() {
                    continue;
                }
                let index = b.call(table.get_index_at(row, column)).await?;
                for (suffix, value) in [
                    ("index", index),
                    (
                        "row_span",
                        b.call(table.get_row_extent_at(row, column)).await?,
                    ),
                    (
                        "column_span",
                        b.call(table.get_column_extent_at(row, column)).await?,
                    ),
                ] {
                    property(
                        out,
                        &format!("cell.{row}.{column}.{suffix}"),
                        "atspi.Table",
                        json!(value),
                        b,
                    );
                }
                let sample = self.sample(&cell, b).await?;
                out["nodes"].as_array_mut().unwrap().push(sample);
            }
        }
        if r["include_headers"] == true {
            for row in first_row..end_row {
                if out["nodes"].as_array().unwrap().len() >= b.limit("max_items") {
                    b.mark("item_limit");
                    break;
                }
                let header = b.call(table.get_row_header(row)).await?;
                if !header.is_null() {
                    let sample = self.sample(&header, b).await?;
                    out["nodes"].as_array_mut().unwrap().push(sample);
                }
                property(
                    out,
                    &format!("row.{row}.description"),
                    "atspi.Table",
                    json!(b.text(b.call(table.get_row_description(row)).await?)),
                    b,
                );
            }
            for column in first_column..end_column {
                if out["nodes"].as_array().unwrap().len() >= b.limit("max_items") {
                    b.mark("item_limit");
                    break;
                }
                let header = b.call(table.get_column_header(column)).await?;
                if !header.is_null() {
                    let sample = self.sample(&header, b).await?;
                    out["nodes"].as_array_mut().unwrap().push(sample);
                }
                property(
                    out,
                    &format!("column.{column}.description"),
                    "atspi.Table",
                    json!(b.text(b.call(table.get_column_description(column)).await?)),
                    b,
                );
            }
        }

        if r["include_selection"] == true {
            for (name, values) in [
                ("selected_rows", b.call(table.get_selected_rows()).await?),
                (
                    "selected_columns",
                    b.call(table.get_selected_columns()).await?,
                ),
            ] {
                if values.len() > b.limit("max_items") {
                    b.mark("item_limit");
                }
                value_property(
                    out,
                    name,
                    "atspi.Table",
                    json!({"kind":"scalars","values":values.into_iter().take(b.limit("max_items")).collect::<Vec<_>>()}),
                    b,
                );
            }
        }
        Ok(())
    }
    async fn hypertext_read(
        &mut self,
        node: &ObjectRefOwned,
        b: &Budget,
        out: &mut Value,
    ) -> Result<(), EIPError> {
        self.require(node, Interface::Hypertext, b).await?;
        let proxy = self.hypertext(node).await?;
        let count = b.call(proxy.get_n_links()).await?.max(0) as usize;
        property(out, "link_count", "atspi.Hypertext", json!(count), b);
        if count > b.limit("max_items") {
            b.mark("item_limit");
        }

        for i in 0..count.min(b.limit("max_items")) {
            if b.check().is_err() {
                break;
            }
            let link = b.call(proxy.get_link(i as i32)).await?;
            if link.is_null() {
                continue;
            }
            let p = self.hyperlink(&link).await?;
            for (name, value) in [
                ("start", json!(b.call(p.start_index()).await?)),
                ("end", json!(b.call(p.end_index()).await?)),
                ("valid", json!(b.call(p.is_valid()).await?)),
            ] {
                property(
                    out,
                    &format!("link.{i}.{name}"),
                    "atspi.Hyperlink",
                    value,
                    b,
                );
            }
            let anchors = b.call(p.n_anchors()).await?.max(0) as usize;
            if anchors > b.limit("max_items") {
                b.mark("item_limit");
            }
            for anchor in 0..anchors.min(b.limit("max_items")) {
                property(
                    out,
                    &format!("link.{i}.uri.{anchor}"),
                    "atspi.Hyperlink",
                    json!(b.text(b.call(p.get_uri(anchor as i32)).await?)),
                    b,
                );
                if out["nodes"].as_array().unwrap().len() >= b.limit("max_items") {
                    b.mark("item_limit");
                    break;
                }
                let object = b.call(p.get_object(anchor as i32)).await?;
                if !object.is_null() {
                    let sample = self.sample(&object, b).await?;
                    out["nodes"].as_array_mut().unwrap().push(sample);
                }
            }
        }

        Ok(())
    }
}
fn property(out: &mut Value, name: &str, source: &str, value: Value, b: &Budget) {
    value_property(out, name, source, json!({"kind":"scalar","value":value}), b);
}
fn value_property(out: &mut Value, name: &str, source: &str, value: Value, b: &Budget) {
    let props = out["properties"]
        .as_array_mut()
        .expect("content properties");
    if name.is_empty() || name.chars().count() > 128 {
        b.mark("item_limit");
        return;
    }
    if props.len() >= b.limit("max_items").min(512) {
        b.mark("item_limit");
        return;
    }
    props.push(json!({"name":name,"source":source,"value":value}));
}
fn append_properties(out: &mut Value, values: Vec<Value>, b: &Budget) {
    for value in values {
        if out["properties"].as_array().expect("properties").len() >= b.limit("max_items") {
            b.mark("item_limit");
            break;
        }
        out["properties"]
            .as_array_mut()
            .expect("properties")
            .push(value);
    }
}
pub(super) fn string_properties(
    values: HashMap<String, String>,
    source: &str,
    b: &Budget,
) -> Vec<Value> {
    let mut values: Vec<_> = values.into_iter().collect();
    values.sort_by(|a, b| a.0.cmp(&b.0));
    if values.len() > b.limit("max_items").min(128) {
        b.mark("item_limit");
    }
    values.into_iter().filter(|(name,_)| {
        let valid = !name.is_empty() && name.chars().count() <= 128;
        if !valid { b.mark("item_limit"); }
        valid
    }).take(b.limit("max_items").min(128)).map(|(name,value)|json!({"name":name,"source":source,"value":{"kind":"scalar","value":b.text(value)}})).collect()
}
