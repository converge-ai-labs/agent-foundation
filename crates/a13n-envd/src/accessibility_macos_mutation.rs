//! Closed AX19 inventory. Every operand is prepared before one native mutation.
use super::super::NativeMutation;
use super::*;
use crate::eip::{AccessibilityMutationResultNativeStatus as Status, ComputerEffect};
use objc2_core_foundation::{CFBoolean, Type};

pub(super) const SETTERS: &[(&str, &str)] = &[
    ("focus", "AXFocused"),
    ("focused_window_set", "AXFocusedWindow"),
    ("main_set", "AXMain"),
    ("minimized_set", "AXMinimized"),
    ("expanded_set", "AXExpanded"),
    ("disclosing_set", "AXDisclosing"),
    ("selected_set", "AXSelected"),
    ("selected_children_set", "AXSelectedChildren"),
    ("selected_rows_set", "AXSelectedRows"),
    ("selected_columns_set", "AXSelectedColumns"),
    ("position_set", "AXPosition"),
    ("size_set", "AXSize"),
    ("value_string_set", "AXValue"),
    ("value_number_set", "AXValue"),
    ("selected_text_set", "AXSelectedText"),
    ("text_range_select", "AXSelectedTextRange"),
    ("text_ranges_select", "AXSelectedTextRanges"),
    ("visible_text_range_set", "AXVisibleCharacterRange"),
];
pub(super) fn content(op: &str) -> bool {
    matches!(
        op,
        "value_string_set"
            | "value_number_set"
            | "selected_text_set"
            | "text_range_select"
            | "text_ranges_select"
            | "visible_text_range_set"
    )
}
pub(super) fn value_type_matches(value: &CFType, op: &str) -> bool {
    match op {
        "value_string_set" => value.downcast_ref::<CFString>().is_some(),
        "value_number_set" => value.downcast_ref::<CFNumber>().is_some(),
        _ => false,
    }
}
fn erase<T: Type + 'static>(v: CFRetained<T>) -> CFRetained<CFType> {
    // SAFETY: all inputs are owned native CF objects; erasure preserves +1.
    unsafe { CFRetained::cast_unchecked(v) }
}
enum Prepared {
    Action(CFRetained<CFString>),
    Set(&'static str, CFRetained<CFType>),
}
impl Native {
    pub(super) fn mutate(
        &mut self,
        method: &str,
        p: &Value,
        b: &Budget,
        dispatch: &mut dyn FnMut() -> Result<(), EIPError>,
    ) -> Result<Value, EIPError> {
        let op = method
            .strip_prefix("computer.ax_")
            .ok_or_else(unsupported)?;
        let r = &p["request"];
        if r.get("op").is_some_and(|v| v != op) {
            return Err(invalid());
        }
        let n = self.node(&p["node"])?;
        // Establish liveness without assuming that every element exposes Enabled.
        get(&n, "AXRole", b)?;
        if optional(get(&n, "AXEnabled", b))?
            .map(|v| native::boolean(&v))
            .transpose()?
            == Some(false)
        {
            return Err(error(ErrorType::InvalidParams, "AX control is disabled"));
        }
        if content(op) && self.protected(&n, b)? {
            return Err(error(ErrorType::Denied, "protected AX control"));
        }
        let prepared = if op == "perform_action" {
            let a = &r["action"];
            if a["node"] != p["node"] {
                return Err(stale());
            }
            let entry = self
                .handles
                .get_mut(string(a, "action_handle")?)
                .ok_or_else(stale)?;
            entry.touched = Instant::now();
            let name = match &entry.value {
                Handle::Action { owner, name } if owner == string(&p["node"], "node_handle")? => {
                    name.clone()
                }
                _ => return Err(stale()),
            };
            if native::name(&name, b)? != string(a, "native_identifier")? {
                return Err(stale());
            }
            let live = native::names(&n, "actions", b)?;
            if !(0..live.len()).any(|i| {
                live.get(i)
                    .is_some_and(|v| v.downcast_ref::<CFString>().is_some_and(|v| v == &*name))
            }) {
                return Err(unsupported());
            }
            Prepared::Action(name)
        } else {
            let key = SETTERS
                .iter()
                .find(|(candidate, _)| *candidate == op)
                .map(|(_, key)| *key)
                .ok_or_else(unsupported)?;
            if !native::writable(&n, key, b)? {
                return Err(unsupported());
            }
            let value = match op {
                "focus" => {
                    unsafe { CFRetained::retain(NonNull::from(CFBoolean::new(true))) }.into()
                }
                "main_set" | "minimized_set" | "expanded_set" | "disclosing_set"
                | "selected_set" => {
                    let value = r["value"].as_bool().ok_or_else(invalid)?;
                    unsafe { CFRetained::retain(NonNull::from(CFBoolean::new(value))) }.into()
                }
                "focused_window_set" => {
                    let window = self.node(&r["window"])?;
                    let role = native::name(&*get(&window, "AXRole", b)?, b)?;
                    if role != "AXWindow"
                        || native::pid(&n, b)? != native::pid(&window, b)?
                        || native::name(&*get(&n, "AXRole", b)?, b)? != "AXApplication"
                    {
                        return Err(invalid());
                    }
                    self.member(&n, "AXWindows", &window, b)?;
                    erase(window)
                }
                "selected_children_set" | "selected_rows_set" | "selected_columns_set" => {
                    let collection = match op {
                        "selected_rows_set" => "AXRows",
                        "selected_columns_set" => "AXColumns",
                        _ => "AXChildren",
                    };
                    let mut nodes = Vec::new();
                    for child in r["children"].as_array().ok_or_else(invalid)? {
                        let child = self.node(child)?;
                        self.member(&n, collection, &child, b)?;
                        nodes.push(child);
                    }
                    erase(objc2_core_foundation::CFArray::from_retained_objects(
                        &nodes,
                    ))
                }
                "position_set" => erase(native::point_value(CGPoint {
                    x: finite(r, "x")?,
                    y: finite(r, "y")?,
                })?),
                "size_set" => {
                    let size = CGSize {
                        width: finite(r, "width")?,
                        height: finite(r, "height")?,
                    };
                    if size.width <= 0.0 || size.height <= 0.0 {
                        return Err(invalid());
                    }
                    erase(native::size_value(size)?)
                }
                "value_string_set" => {
                    // Set the observed Value type, never coerce a numeric control.
                    typed::<CFString>(get(&n, "AXValue", b)?)?;
                    erase(CFString::from_str(string(r, "value")?))
                }
                "value_number_set" => {
                    let current = get(&n, "AXValue", b)?;
                    let current = typed::<CFNumber>(current)?;
                    let value = finite(r, "value")?;
                    if !current.is_float_type()
                        && (value.fract() != 0.0
                            || value < isize::MIN as f64
                            || value >= isize::MAX as f64)
                    {
                        return Err(invalid());
                    }
                    for (key, min) in [("AXMinValue", true), ("AXMaxValue", false)] {
                        if let Some(bound) = optional(get(&n, key, b))? {
                            let bound = native::numeric(&bound)?;
                            if if min { value < bound } else { value > bound } {
                                return Err(invalid());
                            }
                        }
                    }
                    if current.is_float_type() {
                        erase(CFNumber::new_f64(value))
                    } else {
                        erase(CFNumber::new_isize(value as isize))
                    }
                }
                "selected_text_set" => erase(CFString::from_str(string(r, "text")?)),
                "text_range_select" | "visible_text_range_set" => {
                    let span = self.range_operand(&r["range"], &p["node"], b)?;
                    erase(native::span_value(span)?)
                }
                "text_ranges_select" => {
                    let mut spans = Vec::new();
                    for range in r["ranges"].as_array().ok_or_else(invalid)? {
                        spans.push(native::span_value(
                            self.range_operand(range, &p["node"], b)?,
                        )?);
                    }
                    erase(objc2_core_foundation::CFArray::from_retained_objects(
                        &spans,
                    ))
                }
                _ => return Err(unsupported()),
            };
            Prepared::Set(key, value)
        };
        b.check()?;
        dispatch()?;
        let status = unsafe {
            match prepared {
                Prepared::Action(name) => n.perform_action(&name),
                Prepared::Set(key, value) => {
                    n.set_attribute_value(&CFString::from_str(key), &value)
                }
            }
        };
        // Preserve the native return even when optional readback fails or exceeds
        // its remaining budget. Acknowledgement is not application success.
        let observation = if p["readback"].as_bool() == Some(true) {
            self.sample(&n, b)
                .ok()
                .and_then(|v| serde_json::from_value(v).ok())
        } else {
            None
        };
        serde_json::to_value(NativeMutation {
            effect: if status == AXError::Success {
                ComputerEffect::Executed
            } else {
                ComputerEffect::Unknown
            },
            native_status: if status == AXError::Success {
                Status::ReturnedSuccess
            } else {
                Status::Error
            },
            dispatch_complete: true,
            observation,
        })
        .map_err(|_| unavailable())
    }
    fn member(
        &self,
        owner: &AXUIElement,
        key: &str,
        child: &AXUIElement,
        b: &Budget,
    ) -> Result<(), EIPError> {
        let total = native::count(owner, key, b)?;
        for index in 0..total {
            let page = native::page(owner, key, index, 1, b)?;
            if let Some(value) = page.get(0) {
                let candidate = typed::<AXUIElement>(value)?;
                if &*candidate == child {
                    return Ok(());
                }
            }
        }
        Err(error(
            ErrorType::InvalidParams,
            "AX operand does not belong to the required container",
        ))
    }
}
