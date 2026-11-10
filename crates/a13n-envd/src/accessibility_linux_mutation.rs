//! Closed native mutations. All operand reads finish before the one dispatch.
use super::super::NativeMutation;
use super::*;
use crate::eip::{AccessibilityMutationResultNativeStatus as Status, ComputerEffect};

pub(super) const CAPABILITIES: &[(Interface, &[&str])] = &[
    (
        Interface::Component,
        &[
            "focus",
            "scroll_to",
            "scroll_to_point",
            "component_position_set",
            "component_size_set",
            "component_extents_set",
        ],
    ),
    (Interface::Value, &["value_set"]),
    (
        Interface::Selection,
        &[
            "selection_add_child",
            "selection_remove_child",
            "selection_remove_selected",
            "selection_all",
            "selection_clear",
        ],
    ),
    (
        Interface::EditableText,
        &[
            "text_contents_set",
            "text_insert",
            "text_delete",
            "text_copy",
            "text_cut",
            "text_paste",
        ],
    ),
    (
        Interface::Text,
        &[
            "caret_set",
            "text_selection_add",
            "text_selection_set",
            "text_selection_remove",
            "range_scroll_to",
            "range_scroll_to_point",
        ],
    ),
    (Interface::Document, &["document_selections_set"]),
    (
        Interface::Table,
        &[
            "table_row_select",
            "table_row_unselect",
            "table_column_select",
            "table_column_unselect",
        ],
    ),
];
type WireObject = (String, zbus::zvariant::OwnedObjectPath);
pub(super) type WireSelection = (WireObject, i32, WireObject, i32, bool);
// atspi-common 0.14 keeps TextSelection's fields private. Declare just this
// typed native member rather than serializing through private Rust layout.
#[zbus::proxy(interface = "org.a11y.atspi.Document", assume_defaults = true)]
pub(super) trait DocumentSelections {
    fn get_text_selections(&self) -> zbus::Result<Vec<WireSelection>>;
    fn set_text_selections(&self, selections: &[WireSelection]) -> zbus::Result<bool>;
}
impl Native {
    pub(super) async fn document_selections_proxy(
        &self,
        node: &ObjectRefOwned,
    ) -> Result<DocumentSelectionsProxy<'static>, EIPError> {
        DocumentSelectionsProxy::builder(self.connection.as_ref().ok_or_else(unavailable)?)
            .destination(node.name_as_str().ok_or_else(stale)?.to_owned())
            .map_err(native_error)?
            .path(node.path().clone())
            .map_err(native_error)?
            .cache_properties(CacheProperties::No)
            .build()
            .await
            .map_err(native_error)
    }
    pub(super) async fn descendant(
        &self,
        child: &ObjectRefOwned,
        parent: &ObjectRefOwned,
        b: &Budget,
    ) -> Result<bool, EIPError> {
        let mut current = child.clone();
        let mut seen = HashSet::new();
        while seen.len() < 128 && seen.insert(current.clone()) {
            if &current == parent {
                return Ok(true);
            }
            let p = self.accessible(&current).await?;
            current = b.call(p.parent()).await?;
            if current.is_null() {
                break;
            }
        }
        Ok(false)
    }
    async fn child_index(
        &self,
        node: &ObjectRefOwned,
        child: &ObjectRefOwned,
        b: &Budget,
    ) -> Result<i32, EIPError> {
        let p = self.accessible(child).await?;
        if b.call(p.parent()).await? != *node {
            return Err(stale());
        }
        let index = b.call(p.get_index_in_parent()).await?;
        if index < 0
            || b.call(self.accessible(node).await?.get_child_at_index(index))
                .await?
                != *child
        {
            return Err(stale());
        }
        Ok(index)
    }
    pub(super) async fn mutate(
        &mut self,
        method: &str,
        p: &Value,
        b: &Budget,
        dispatch: &mut dyn FnMut() -> Result<(), EIPError>,
    ) -> Result<Value, EIPError> {
        let node = self.node(&p["node"])?;
        let r = &p["request"];
        // Each negotiated mutation RPC fixes its operation; reject a conflicting
        // discriminator before reading operands or dispatching a native call.
        let op = method
            .strip_prefix("computer.atspi_")
            .ok_or_else(|| error(ErrorType::Unsupported, "unsupported AT-SPI mutation"))?;
        if r.get("op").is_some_and(|value| value != op) {
            return Err(error(
                ErrorType::InvalidParams,
                "native mutation and method disagree",
            ));
        }
        let interface = if op == "do_action" {
            Interface::Action
        } else {
            CAPABILITIES
                .iter()
                .find_map(|(i, ops)| ops.contains(&op).then_some(*i))
                .ok_or_else(|| error(ErrorType::Unsupported, "unsupported mutation"))?
        };
        self.require(&node, interface, b).await?;
        if self.protected(&node, b).await? {
            return Err(error(ErrorType::Denied, "protected accessibility control"));
        }
        // Every branch constructs and validates operands before entering this
        // macro. No readback, validation error, or lost reply replays a call.
        macro_rules! apply {
            ($future:expr) => {{
                b.check()?;
                dispatch()?;
                b.calls.set(b.calls.get() + 1);
                // A pending dispatch stays under the parent's hard deadline.
                // Only protocol-defined refusals prove no native execution.
                match $future.await {
                    Ok(value) => value,
                    Err(err) if definite_refusal(&err) => {
                        return acknowledgement(false, Status::Error);
                    }
                    Err(err) => return Err(native_error(err)),
                }
            }};
        }
        let success = match op {
            "do_action" => {
                if r["action"]["node"] != p["node"] {
                    return Err(stale());
                }
                let Handle::Action {
                    node: owner,
                    index,
                    identity,
                } = self.registry.get(string(&r["action"], "action_handle")?)?
                else {
                    return Err(stale());
                };
                if owner != node
                    || identity.get(index as usize).map(|i| i.0.as_str())
                        != r["action"]["native_identifier"].as_str()
                {
                    return Err(stale());
                }
                if self.actions(&node, b).await? != identity {
                    return Err(stale());
                }
                let proxy = self.action(&node).await?;
                apply!(proxy.do_action(index))
            }
            "focus" => {
                let proxy = self.component(&node).await?;
                apply!(proxy.grab_focus())
            }
            "value_set" => {
                let value = r["value"]
                    .as_f64()
                    .filter(|v| v.is_finite())
                    .ok_or_else(|| {
                        error(ErrorType::InvalidParams, "finite native value required")
                    })?;
                let proxy = self.value(&node).await?;
                let min = b.call(proxy.minimum_value()).await?;
                let max = b.call(proxy.maximum_value()).await?;
                if value < min || value > max {
                    return Err(error(
                        ErrorType::InvalidParams,
                        "value outside native bounds",
                    ));
                }
                apply!(proxy.set_current_value(value));
                true
            }
            "selection_add_child" | "selection_remove_child" | "selection_remove_selected" => {
                let child = self.node(&r["child"])?;
                self.live(&child, b).await?;
                let mut index = self.child_index(&node, &child, b).await?;
                let proxy = self.selection(&node).await?;
                if op == "selection_remove_selected" {
                    let count = b.call(proxy.n_selected_children()).await?;
                    let mut selected = None;
                    for i in 0..count.min(512) {
                        if b.call(proxy.get_selected_child(i)).await? == child {
                            selected = Some(i);
                            break;
                        }
                    }
                    index = selected.ok_or_else(stale)?;
                }
                match op {
                    "selection_add_child" => apply!(proxy.select_child(index)),
                    "selection_remove_child" => apply!(proxy.deselect_child(index)),
                    _ => apply!(proxy.deselect_selected_child(index)),
                }
            }
            "selection_all" | "selection_clear" => {
                let proxy = self.selection(&node).await?;
                if op == "selection_all" {
                    apply!(proxy.select_all())
                } else {
                    apply!(proxy.clear_selection())
                }
            }
            "text_contents_set" => {
                let text = string(r, "text")?;
                let proxy = self.editable(&node).await?;
                apply!(proxy.set_text_contents(text))
            }
            "text_insert" | "text_paste" | "caret_set" => {
                let position = integer(r, "position")?;
                self.valid_span(&node, position, position, b).await?;
                match op {
                    "text_insert" => {
                        let text = string(r, "text")?;
                        let length = i32::try_from(text.len()).map_err(|_| {
                            error(ErrorType::InvalidParams, "text exceeds native byte length")
                        })?;
                        let proxy = self.editable(&node).await?;
                        apply!(proxy.insert_text(position, text, length))
                    }
                    "text_paste" => {
                        let proxy = self.editable(&node).await?;
                        apply!(proxy.paste_text(position))
                    }
                    _ => {
                        let proxy = self.text(&node).await?;
                        apply!(proxy.set_caret_offset(position))
                    }
                }
            }
            "text_delete"
            | "text_copy"
            | "text_cut"
            | "text_selection_add"
            | "text_selection_set"
            | "range_scroll_to"
            | "range_scroll_to_point" => {
                let (owner, start, end) = self.span(&r["range"])?;
                if owner != node {
                    return Err(stale());
                }
                self.valid_span(&node, start, end, b).await?;
                match op {
                    "text_delete" => {
                        let proxy = self.editable(&node).await?;
                        apply!(proxy.delete_text(start, end))
                    }
                    "text_copy" => {
                        let proxy = self.editable(&node).await?;
                        apply!(proxy.copy_text(start, end));
                        true
                    }
                    "text_cut" => {
                        let proxy = self.editable(&node).await?;
                        apply!(proxy.cut_text(start, end))
                    }
                    "text_selection_add" => {
                        let proxy = self.text(&node).await?;
                        apply!(proxy.add_selection(start, end))
                    }
                    "text_selection_set" => {
                        let index = integer(r, "index")?;
                        let proxy = self.text(&node).await?;
                        if index < 0 || index >= b.call(proxy.get_n_selections()).await? {
                            return Err(stale());
                        }
                        apply!(proxy.set_selection(index, start, end))
                    }
                    "range_scroll_to" => {
                        let alignment = alignment(r)?;
                        let proxy = self.text(&node).await?;
                        apply!(proxy.scroll_substring_to(start, end, alignment as u32))
                    }
                    _ => {
                        let (frame, x, y) = point(&r["point"])?;
                        let proxy = self.text(&node).await?;
                        apply!(proxy.scroll_substring_to_point(start, end, frame as u32, x, y))
                    }
                }
            }
            "text_selection_remove" => {
                let index = integer(r, "index")?;
                let proxy = self.text(&node).await?;
                if index < 0 || index >= b.call(proxy.get_n_selections()).await? {
                    return Err(stale());
                }
                apply!(proxy.remove_selection(index))
            }
            "document_selections_set" => {
                let mut selections = Vec::new();
                for selection in r["selections"].as_array().ok_or_else(|| {
                    error(ErrorType::InvalidParams, "document selections required")
                })? {
                    let start = self.node(&selection["start"])?;
                    let end = self.node(&selection["end"])?;
                    if !self.descendant(&start, &node, b).await?
                        || !self.descendant(&end, &node, b).await?
                    {
                        return Err(stale());
                    }
                    if self.protected(&start, b).await? || self.protected(&end, b).await? {
                        return Err(error(ErrorType::Denied, "protected document endpoint"));
                    }
                    let from = integer(selection, "start_offset")?;
                    let to = integer(selection, "end_offset")?;
                    self.valid_span(&start, from, from, b).await?;
                    self.valid_span(&end, to, to, b).await?;
                    selections.push((
                        (
                            start.name_as_str().ok_or_else(stale)?.to_owned(),
                            start.path().clone().into(),
                        ),
                        from,
                        (
                            end.name_as_str().ok_or_else(stale)?.to_owned(),
                            end.path().clone().into(),
                        ),
                        to,
                        selection["start_is_active"].as_bool().unwrap_or(false),
                    ));
                }
                let proxy = self.document_selections_proxy(&node).await?;
                apply!(proxy.set_text_selections(&selections))
            }
            "scroll_to" => {
                let alignment = alignment(r)?;
                let proxy = self.component(&node).await?;
                apply!(proxy.scroll_to(alignment))
            }
            "scroll_to_point" | "component_position_set" | "component_extents_set" => {
                let (frame, x, y) = point(&r["point"])?;
                let proxy = self.component(&node).await?;
                match op {
                    "scroll_to_point" => apply!(proxy.scroll_to_point(frame, x, y)),
                    "component_position_set" => apply!(proxy.set_position(x, y, frame)),
                    _ => {
                        let width = integer(r, "width")?;
                        let height = integer(r, "height")?;
                        if width <= 0 || height <= 0 {
                            return Err(error(
                                ErrorType::InvalidParams,
                                "positive native dimensions required",
                            ));
                        }
                        apply!(proxy.set_extents(x, y, width, height, frame))
                    }
                }
            }
            "component_size_set" => {
                let width = integer(r, "width")?;
                let height = integer(r, "height")?;
                if width <= 0 || height <= 0 {
                    return Err(error(
                        ErrorType::InvalidParams,
                        "positive native dimensions required",
                    ));
                }
                let proxy = self.component(&node).await?;
                apply!(proxy.set_size(width, height))
            }
            "table_row_select"
            | "table_row_unselect"
            | "table_column_select"
            | "table_column_unselect" => {
                let index = integer(r, "index")?;
                let proxy = self.table(&node).await?;
                let count = if op.starts_with("table_row") {
                    b.call(proxy.n_rows()).await?
                } else {
                    b.call(proxy.n_columns()).await?
                };
                if index < 0 || index >= count {
                    return Err(error(
                        ErrorType::InvalidParams,
                        "table index outside native bounds",
                    ));
                }
                match op {
                    "table_row_select" => apply!(proxy.add_row_selection(index)),
                    "table_row_unselect" => apply!(proxy.remove_row_selection(index)),
                    "table_column_select" => apply!(proxy.add_column_selection(index)),
                    _ => apply!(proxy.remove_column_selection(index)),
                }
            }
            _ => return Err(error(ErrorType::Unsupported, "unsupported mutation")),
        };
        // The parent receives this acknowledgement before optional readback.
        acknowledgement(
            success,
            if success {
                Status::ReturnedSuccess
            } else {
                Status::ReturnedFalse
            },
        )
    }
}
pub(super) fn definite_refusal(err: &zbus::Error) -> bool {
    // InvalidArgs and AccessDenied can come from an application handler after
    // partial effects. Only an unavailable native method proves nonexecution.
    match err {
        zbus::Error::MethodError(name, _, _) => {
            matches!(name.as_str(), "org.freedesktop.DBus.Error.UnknownMethod")
        }
        // Properties.Set converts the same replies through the typed FDO proxy.
        zbus::Error::FDO(err) => matches!(err.as_ref(), zbus::fdo::Error::UnknownMethod(_)),
        _ => false,
    }
}
fn acknowledgement(success: bool, native_status: Status) -> Result<Value, EIPError> {
    serde_json::to_value(NativeMutation {
        effect: if success {
            ComputerEffect::Executed
        } else {
            ComputerEffect::NotExecuted
        },
        native_status,
        dispatch_complete: true,
        observation: None,
    })
    .map_err(|_| error(ErrorType::InternalError, "cannot encode native mutation"))
}
fn alignment(r: &Value) -> Result<ScrollType, EIPError> {
    match r["alignment"].as_str() {
        Some("top_left") => Ok(ScrollType::TopLeft),
        Some("bottom_right") => Ok(ScrollType::BottomRight),
        Some("top_edge") => Ok(ScrollType::TopEdge),
        Some("bottom_edge") => Ok(ScrollType::BottomEdge),
        Some("left_edge") => Ok(ScrollType::LeftEdge),
        Some("right_edge") => Ok(ScrollType::RightEdge),
        Some("anywhere") => Ok(ScrollType::Anywhere),
        _ => Err(error(
            ErrorType::InvalidParams,
            "native scroll alignment required",
        )),
    }
}
