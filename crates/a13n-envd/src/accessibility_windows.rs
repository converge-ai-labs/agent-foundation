//! UI Automation runs exclusively on the helper's initial MTA thread. COM
//! objects never cross IPC or thread boundaries; the Session owns its deadline.
#![allow(
    unsafe_code,
    reason = "UI Automation is a COM API with thread-affine native objects"
)]
use super::{Backend, EIPError, ErrorType, error};
use serde_json::{Value, json};
use std::{
    cell::Cell,
    collections::{HashMap, HashSet},
    time::{Duration, Instant},
};
use unicode_casefold::UnicodeCaseFold;
use windows::{
    Win32::{
        Foundation::{POINT, RECT},
        System::{
            Com::{
                CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx,
                CoUninitialize, SAFEARRAY,
            },
            Ole::*,
            Variant::*,
        },
        UI::Accessibility::*,
    },
    core::{BSTR, Interface},
};

#[path = "accessibility_windows_mutation.rs"]
mod mutation;
#[path = "accessibility_windows_range.rs"]
mod range;
#[path = "accessibility_windows_read.rs"]
mod read;
use mutation::content_mutation;
use read::{OwnedArray, property_value, scalar_variant};

pub(super) const METHODS: &[&str] = &[
    "computer.a11y_describe",
    "computer.a11y_roots",
    "computer.a11y_inspect",
    "computer.a11y_find",
    "computer.a11y_read",
    "computer.a11y_range",
    "computer.a11y_range_read",
    "computer.uia_collapse",
    "computer.uia_dock_set",
    "computer.uia_expand",
    "computer.uia_focus",
    "computer.uia_invoke",
    "computer.uia_legacy_default_action",
    "computer.uia_legacy_select",
    "computer.uia_legacy_value_set",
    "computer.uia_range_add_selection",
    "computer.uia_range_remove_selection",
    "computer.uia_range_scroll_into_view",
    "computer.uia_range_select",
    "computer.uia_range_show_context_menu",
    "computer.uia_range_value_set",
    "computer.uia_realize",
    "computer.uia_scroll_amount",
    "computer.uia_scroll_into_view",
    "computer.uia_scroll_percent",
    "computer.uia_selection_add",
    "computer.uia_selection_remove",
    "computer.uia_selection_select",
    "computer.uia_show_context_menu",
    "computer.uia_toggle",
    "computer.uia_transform_move",
    "computer.uia_transform_resize",
    "computer.uia_transform_rotate",
    "computer.uia_transform_zoom",
    "computer.uia_transform_zoom_unit",
    "computer.uia_value_set",
    "computer.uia_view_set",
    "computer.uia_window_close",
    "computer.uia_window_state_set",
];

struct Apartment;
impl Drop for Apartment {
    fn drop(&mut self) {
        unsafe { CoUninitialize() }
    }
}
pub(super) fn new(epoch: &str) -> Result<Box<dyn Backend>, EIPError> {
    let dpi = crate::computer::windows::Dpi::enter()?;
    unsafe {
        CoInitializeEx(None, COINIT_MULTITHREADED)
            .ok()
            .map_err(native_error)?;
    }
    let apartment = Apartment;
    let automation = unsafe { CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER) }
        .map_err(native_error)?;
    Ok(Box::new(Native {
        epoch: epoch.into(),
        handles: HashMap::new(),
        identities: HashMap::new(),
        next: 0,
        automation,
        _apartment: apartment,
        _dpi: dpi,
    }))
}

struct Native {
    epoch: String,
    handles: HashMap<String, Entry>,
    identities: HashMap<Vec<i32>, String>,
    next: u64,
    automation: IUIAutomation,
    // Drop COM objects before uninitializing their apartment.
    _apartment: Apartment,
    _dpi: crate::computer::windows::Dpi,
}
struct Entry {
    value: Handle,
    touched: Instant,
}
#[derive(Clone)]
enum Handle {
    Node(IUIAutomationElement),
    Range {
        owner: String,
        value: IUIAutomationTextRange,
    },
    Cursor {
        root: Value,
        query: Value,
        view: String,
        depth: usize,
        pending: Vec<Pending>,
    },
}
#[derive(Clone)]
enum Pending {
    Node(IUIAutomationElement, usize, Option<IUIAutomationElement>),
    Children(IUIAutomationElement, usize),
    Sibling(IUIAutomationElement, usize, IUIAutomationElement),
}

struct Budget {
    limits: Value,
    calls: Cell<usize>,
    reason: Cell<Option<&'static str>>,
    deadline: Instant,
    projection: usize,
}
impl Budget {
    fn new(params: &Value) -> Result<Self, EIPError> {
        let limits = params["limits"].clone();
        let projection = params["projection_observation_id"]
            .as_str()
            .map(super::budget::projection_reserve)
            .transpose()?
            .unwrap_or(0);
        let timeout =
            number(params, "_timeout_ms", 5000).min(number(&limits, "timeout_ms", 5000)) as u64;
        // Leave time to encode the partial response before the parent deadline.
        let reserve = (timeout / 10).min(50);
        Ok(Self {
            deadline: Instant::now() + Duration::from_millis(timeout - reserve),
            limits,
            calls: Cell::new(0),
            reason: Cell::new(None),
            projection,
        })
    }
    fn limit(&self, key: &str) -> usize {
        number(
            &self.limits,
            key,
            match key {
                "depth" => 2,
                "max_nodes" => 256,
                "max_matches" => 64,
                "max_items" => 128,
                "max_text_bytes" => 16384,
                "max_result_bytes" => 262144,
                "max_native_calls" => 1024,
                _ => 5000,
            },
        )
    }
    fn mark(&self, reason: &'static str) {
        if self.reason.get().is_none() || matches!(reason, "deadline" | "call_limit") {
            self.reason.set(Some(reason));
        }
    }
    fn check(&self) -> Result<(), EIPError> {
        if Instant::now() >= self.deadline {
            self.mark("deadline");
        }
        if self.calls.get() >= self.limit("max_native_calls") {
            self.mark("call_limit");
        }
        if matches!(self.reason.get(), Some("deadline" | "call_limit")) {
            Err(error(
                ErrorType::Timeout,
                "accessibility native budget exhausted",
            ))
        } else {
            Ok(())
        }
    }
    fn call<T>(&self, f: impl FnOnce() -> windows::core::Result<T>) -> Result<T, EIPError> {
        self.check()?;
        self.calls.set(self.calls.get() + 1);
        f().map_err(native_error)
    }
    fn completeness(&self) -> Value {
        match self.reason.get() {
            Some(r) => json!({"complete":false,"reason":r}),
            None => json!({"complete":true}),
        }
    }
    fn text(&self, mut value: String) -> String {
        let max = self.limit("max_text_bytes");
        if value.len() > max {
            let mut end = max;
            while !value.is_char_boundary(end) {
                end -= 1;
            }
            value.truncate(end);
            self.mark("byte_limit");
        }
        value
    }
    fn fits(&self, method: &str, value: &Value) -> Result<bool, EIPError> {
        Ok(super::budget::result_bytes(method, value, self.projection)?
            <= self.limit("max_result_bytes"))
    }
}
impl Backend for Native {
    fn execute(
        &mut self,
        method: &str,
        params: Value,
        dispatch: &mut dyn FnMut() -> Result<(), EIPError>,
    ) -> Result<Value, EIPError> {
        // Evict only between requests. Operands and newly returned handles stay
        // retained through native dispatch and response construction.
        self.prune(&params);
        let b = Budget::new(&params)?;
        if method == "computer.a11y_describe" {
            let ready = crate::computer::windows::ready().is_ok();
            return Ok(
                json!({"backend":"uia","backend_epoch":self.epoch,"ready":ready,"reason":if ready {"ready"} else {"desktop_unavailable"},"desktop_identity":"inherited Windows interactive user desktop","methods":METHODS,"frames":["uia_physical_screen"],"limits":{}}),
            );
        }
        crate::computer::windows::ready()?;
        let mut value = match method {
            "computer.a11y_roots" => self.roots(&params, &b),
            "computer.a11y_inspect" | "computer.a11y_find" => self.tree(method, &params, &b),
            "computer.a11y_read" => self.read(&params, &b),
            "computer.a11y_range" => self.range(&params, &b),
            "computer.a11y_range_read" => self.range_read(&params, &b),
            _ => return self.mutate(method, &params, &b, dispatch),
        }?;
        if !matches!(method, "computer.a11y_inspect" | "computer.a11y_find") {
            fit_result(&mut value, method, &b)?;
        }
        Ok(value)
    }
}
impl Native {
    fn prune(&mut self, params: &Value) {
        let mut dependencies = HashSet::new();
        let mut retain_reference = |reference: &Value| {
            for key in ["node_handle", "range_handle", "cursor_handle"] {
                if let Some(id) = reference[key].as_str() {
                    dependencies.insert(id.to_owned());
                }
            }
        };
        retain_operand_references(params, &mut retain_reference);
        for entry in self
            .handles
            .values()
            .filter(|e| e.touched.elapsed() < Duration::from_secs(120))
        {
            match &entry.value {
                Handle::Range { owner, .. } => retain_reference(&json!({"node_handle":owner})),
                Handle::Cursor { root, .. } => retain_reference(root),
                Handle::Node(_) => {}
            }
        }
        self.handles.retain(|id, e| {
            e.touched.elapsed() < Duration::from_secs(120) || dependencies.contains(id)
        });
        // Free capacity between requests only; never evict an executing operand
        // or a live range/cursor dependency. Native objects held by Pending are
        // owned directly, without minting model handles for unmatched nodes.
        let mut candidates: Vec<_> = self
            .handles
            .iter()
            .filter(|(id, e)| !dependencies.contains(*id) && matches!(e.value, Handle::Node(_)))
            .map(|(id, e)| (id.clone(), e.touched))
            .collect();
        candidates.sort_unstable_by_key(|(_, touched)| *touched);
        let mut bytes: usize = self
            .handles
            .values()
            .map(|e| retained_bytes(&e.value))
            .sum();
        for (id, _) in candidates {
            if self.handles.len() < 2048 && bytes < 4 * 1024 * 1024 {
                break;
            }
            if let Some(entry) = self.handles.remove(&id) {
                bytes = bytes.saturating_sub(retained_bytes(&entry.value));
            }
        }
        self.identities
            .retain(|_, id| self.handles.contains_key(id));
    }
    fn put(&mut self, prefix: &str, value: Handle) -> Result<String, EIPError> {
        let ranges = self
            .handles
            .values()
            .filter(|e| matches!(e.value, Handle::Range { .. }))
            .count();
        let cursors = self
            .handles
            .values()
            .filter(|e| matches!(e.value, Handle::Cursor { .. }))
            .count();
        let bytes: usize = self
            .handles
            .values()
            .map(|e| retained_bytes(&e.value))
            .sum();
        if self.handles.len() >= 4096
            || bytes.saturating_add(retained_bytes(&value)) > 8 * 1024 * 1024
            || matches!(&value, Handle::Range { .. }) && ranges >= 256
            || matches!(&value, Handle::Cursor { .. }) && cursors >= 64
        {
            return Err(error(
                ErrorType::QuotaExceeded,
                "UIA handle registry is full",
            ));
        }
        self.next += 1;
        let id = format!("{prefix}_{:x}", self.next);
        self.handles.insert(
            id.clone(),
            Entry {
                value,
                touched: Instant::now(),
            },
        );
        Ok(id)
    }
    fn reference(&mut self, element: IUIAutomationElement, b: &Budget) -> Result<Value, EIPError> {
        let array = OwnedArray(b.call(|| unsafe { element.GetRuntimeId() })?);
        let runtime_id = array.runtime_id(b)?;
        let mut existing = if runtime_id.is_empty() {
            // The desktop root can lack a runtime ID. Reuse its COM identity.
            self.handles.iter().find_map(|(id, e)| match &e.value {
                Handle::Node(n) if n == &element => Some(id.clone()),
                _ => None,
            })
        } else {
            self.identities.get(&runtime_id).cloned()
        };
        if let Some(id) = &existing
            && let Some(Entry {
                value: Handle::Node(retained),
                ..
            }) = self.handles.get(id)
        {
            match b.call(|| unsafe { retained.CurrentControlType() }) {
                Ok(_) => {}
                Err(e) if element_unavailable(&e) => {
                    // Runtime IDs may be reused after destruction. A new native
                    // object must never replace the object behind an old handle.
                    self.handles.remove(id);
                    self.identities.remove(&runtime_id);
                    existing = None;
                }
                Err(e) => return Err(e),
            }
        }
        let id = match existing {
            Some(id) => {
                self.handles.get_mut(&id).unwrap().touched = Instant::now();
                id
            }
            None => self.put("an", Handle::Node(element))?,
        };
        if !runtime_id.is_empty() {
            self.identities.insert(runtime_id, id.clone());
        }
        Ok(json!({"backend":"uia","backend_epoch":self.epoch,"node_handle":id}))
    }
    fn node(&mut self, reference: &Value) -> Result<IUIAutomationElement, EIPError> {
        if reference["backend"] != "uia" || reference["backend_epoch"] != self.epoch {
            return Err(stale());
        }
        let entry = self
            .handles
            .get_mut(string(reference, "node_handle")?)
            .ok_or_else(stale)?;
        entry.touched = Instant::now();
        match &entry.value {
            Handle::Node(n) => Ok(n.clone()),
            _ => Err(stale()),
        }
    }
    fn pattern<T: Interface>(
        &self,
        n: &IUIAutomationElement,
        id: UIA_PATTERN_ID,
        b: &Budget,
    ) -> Result<T, EIPError> {
        b.call(|| unsafe { n.GetCurrentPatternAs(id) })
            .map_err(pattern_error)
    }
    fn protected(&self, n: &IUIAutomationElement, b: &Budget) -> Result<bool, EIPError> {
        b.call(|| unsafe { n.CurrentIsPassword() })
            .map(|v| v.as_bool())
    }
    fn walker(&self, view: &str, b: &Budget) -> Result<IUIAutomationTreeWalker, EIPError> {
        b.call(|| unsafe {
            match view {
                "raw" => self.automation.RawViewWalker(),
                "content" => self.automation.ContentViewWalker(),
                _ => self.automation.ControlViewWalker(),
            }
        })
    }
    fn sample(&mut self, n: &IUIAutomationElement, b: &Budget) -> Result<Value, EIPError> {
        let mut result = self.sample_fields(n, b)?;
        result["reference"] = self.reference(n.clone(), b)?;
        Ok(result)
    }
    fn sample_fields(&self, n: &IUIAutomationElement, b: &Budget) -> Result<Value, EIPError> {
        let role = b.call(|| unsafe { n.CurrentControlType() })?;
        let mut result = json!({"reference":{"backend":"uia","backend_epoch":self.epoch,"node_handle":"x".repeat(128)},"native_role":format!("UIA_ControlType_{}",role.0),"role":role_name(role),"sampled_at":now(),"states":{},"capabilities":[],"field_errors":[]});
        macro_rules! field {
            ($name:literal,$call:expr) => {
                match b.call(|| unsafe { $call }) {
                    Ok(value) => result[$name] = json!(b.text(value.to_string())),
                    Err(e) => field_error(&mut result, $name, &e),
                }
            };
        }
        field!("name", n.CurrentName());
        field!("identifier", n.CurrentAutomationId());
        field!("description", n.CurrentHelpText());
        macro_rules! state {
            ($name:literal,$call:expr) => {
                match b.call(|| unsafe { $call }) {
                    Ok(value) => result["states"][$name] = json!(value.as_bool()),
                    Err(e) => field_error(&mut result, $name, &e),
                }
            };
        }
        state!("enabled", n.CurrentIsEnabled());
        state!("focused", n.CurrentHasKeyboardFocus());
        state!("focusable", n.CurrentIsKeyboardFocusable());
        state!("offscreen", n.CurrentIsOffscreen());
        state!("protected", n.CurrentIsPassword());
        match b.call(|| unsafe { n.CurrentBoundingRectangle() }) {
            Ok(r) if r.right >= r.left && r.bottom >= r.top => result["bounds"] = bounds(r),
            Ok(_) => field_error(
                &mut result,
                "bounds",
                &error(ErrorType::ProviderUnavailable, "invalid UIA rectangle"),
            ),
            Err(e) => field_error(&mut result, "bounds", &e),
        }
        if result["states"]["enabled"] == false {
            return Ok(result);
        }
        if result["states"]["focusable"] == true {
            result["capabilities"]
                .as_array_mut()
                .unwrap()
                .push(capability("focus", "IUIAutomationElement"));
        }
        for &(id, source, operations) in mutation::PATTERNS {
            match b
                .call(|| unsafe { n.GetCurrentPattern(id) })
                .map_err(pattern_error)
            {
                Ok(_) => {
                    for op in operations {
                        if result["states"]["protected"] != false && content_mutation(op) {
                            continue;
                        }
                        match self.operation_support(n, op, b) {
                            Ok(Some(support)) => {
                                let mut cap = capability(op, source);
                                cap["supported"] = json!(support);
                                result["capabilities"].as_array_mut().unwrap().push(cap);
                            }
                            Ok(None) => {}
                            Err(e) => field_error(&mut result, op, &e),
                        }
                    }
                }
                Err(e) if e.data.error_type == ErrorType::Unsupported => {}
                Err(e) => {
                    field_error(&mut result, source, &e);
                    if b.reason.get().is_some() {
                        break;
                    }
                }
            }
        }
        if b.call(|| n.cast::<IUIAutomationElement3>()).is_ok() {
            result["capabilities"]
                .as_array_mut()
                .unwrap()
                .push(capability("show_context_menu", "IUIAutomationElement3"));
        }
        if result["states"]["protected"] == false
            && let Ok(pattern) = self.pattern::<IUIAutomationTextPattern>(n, UIA_TextPatternId, b)
            && b.call(|| unsafe { pattern.DocumentRange() })
                .and_then(|range| b.call(|| range.cast::<IUIAutomationTextRange2>()))
                .is_ok()
        {
            result["capabilities"]
                .as_array_mut()
                .unwrap()
                .push(capability("range_show_context_menu", "TextRange2"));
        }
        Ok(result)
    }
    fn roots(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let scope = &p["scope"];
        let mut result = tree_result();
        let acquired = (|| {
            match scope["kind"].as_str().unwrap_or("windows") {
                "focused" => {
                    let n = b.call(|| unsafe { self.automation.GetFocusedElement() })?;
                    result["nodes"]
                        .as_array_mut()
                        .unwrap()
                        .push(self.sample(&n, b)?);
                }
                "desktop" => {
                    let n = b.call(|| unsafe { self.automation.GetRootElement() })?;
                    result["nodes"]
                        .as_array_mut()
                        .unwrap()
                        .push(self.sample(&n, b)?);
                }
                "at_point" => {
                    let point = native_point(&scope["point"])?;
                    let n = b.call(|| unsafe { self.automation.ElementFromPoint(point) })?;
                    result["nodes"]
                        .as_array_mut()
                        .unwrap()
                        .push(self.sample(&n, b)?);
                }
                "windows" | "applications" | "application" => {
                    if scope["kind"] == "application" && scope["process_id"].as_i64().is_none() {
                        return Err(invalid());
                    }
                    let root = b.call(|| unsafe { self.automation.GetRootElement() })?;
                    let walker = self.walker("control", b)?;
                    let mut current =
                        optional(b.call(|| unsafe { walker.GetFirstChildElement(&root) }))?;
                    while let Some(n) = current {
                        if result["nodes"].as_array().unwrap().len() >= b.limit("max_nodes") {
                            b.mark("node_limit");
                            break;
                        }
                        let matches = match scope["process_id"].as_i64() {
                            Some(pid) => {
                                i64::from(b.call(|| unsafe { n.CurrentProcessId() })?) == pid
                            }
                            None => true,
                        };
                        if matches {
                            let sample = self.sample(&n, b)?;
                            result["nodes"].as_array_mut().unwrap().push(sample);
                            if !b.fits("computer.a11y_roots", &result)? {
                                result["nodes"].as_array_mut().unwrap().pop();
                                b.mark("byte_limit");
                                break;
                            }
                        }
                        current = optional(b.call(|| unsafe { walker.GetNextSiblingElement(&n) }))?;
                    }
                }
                _ => return Err(unsupported()),
            }
            Ok(())
        })();
        partial(acquired, b)?;
        result["completeness"] = b.completeness();
        Ok(result)
    }
    fn tree(&mut self, method: &str, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let root = p["node"].clone();
        let root_node = self.node(&root)?;
        let query = if method == "computer.a11y_find" {
            p["query"].clone()
        } else {
            Value::Null
        };
        let view = p["view"].as_str().unwrap_or("control").to_owned();
        let depth = b.limit("depth");
        let mut pending = if p["cursor"].is_object() {
            if p["cursor"]["root"] != root {
                return Err(stale());
            }
            let id = string(&p["cursor"], "cursor_handle")?;
            let entry = self.handles.get(id).ok_or_else(stale)?;
            match &entry.value {
                Handle::Cursor {
                    root: r,
                    query: q,
                    view: v,
                    depth: d,
                    pending,
                } if r == &root && q == &query && v == &view && d == &depth => pending.clone(),
                _ => return Err(stale()),
            }
        } else {
            vec![Pending::Node(root_node, 0, None)]
        };
        let walker = self.walker(&view, b)?;
        let mut result = tree_result();
        let mut visited = 0;
        let outcome = (|| {
            while let Some(item) = pending.last().cloned() {
                b.check()?;
                if visited >= b.limit("max_nodes") {
                    b.mark("node_limit");
                    break;
                }
                if method == "computer.a11y_find"
                    && result["nodes"].as_array().unwrap().len() >= b.limit("max_matches")
                {
                    b.mark("total_limit");
                    break;
                }
                match item {
                    Pending::Children(n, d) => {
                        // Keep this stage queued until acquisition succeeds. A
                        // deadline after sampling must not skip the subtree.
                        let child = traversal_optional(
                            b.call(|| unsafe { walker.GetFirstChildElement(&n) }),
                            b,
                        )?;
                        pending.pop();
                        if let Some(child) = child {
                            pending.push(Pending::Sibling(child.clone(), d, n.clone()));
                            pending.push(Pending::Node(child, d, Some(n)));
                        }
                    }
                    Pending::Sibling(n, d, parent) => {
                        // A missing sibling anchor is not an observed end of
                        // enumeration. Fail this cursor so callers reacquire;
                        // silently skipping it would hide still-live siblings.
                        let next =
                            optional(b.call(|| unsafe { walker.GetNextSiblingElement(&n) }))?;
                        pending.pop();
                        if let Some(next) = next {
                            pending.push(Pending::Sibling(next.clone(), d, parent.clone()));
                            pending.push(Pending::Node(next, d, Some(parent)));
                        }
                    }
                    Pending::Node(n, d, parent) => {
                        let Some(mut sample) = traversal_optional(self.sample_fields(&n, b), b)?
                        else {
                            pending.pop();
                            visited += 1;
                            continue;
                        };
                        if matches_query(&sample, &query) {
                            if parent.is_some() {
                                sample["parent"] = sample["reference"].clone();
                            }
                            result["nodes"].as_array_mut().unwrap().push(sample);
                            if !page_fits(method, &result, &root, b)? {
                                result["nodes"].as_array_mut().unwrap().pop();
                                b.mark("byte_limit");
                                break;
                            }
                            let mut sample = result["nodes"].as_array_mut().unwrap().pop().unwrap();
                            // Mint only after the page can retain this node.
                            let Some(reference) =
                                traversal_optional(self.reference(n.clone(), b), b)?
                            else {
                                pending.pop();
                                visited += 1;
                                continue;
                            };
                            let parent = match parent {
                                Some(parent) => traversal_optional(self.reference(parent, b), b)?,
                                None => None,
                            };
                            sample["reference"] = reference;
                            sample.as_object_mut().unwrap().remove("parent");
                            if let Some(parent) = parent {
                                sample["parent"] = parent;
                            }
                            result["nodes"].as_array_mut().unwrap().push(sample);
                        }
                        pending.pop();
                        visited += 1;
                        if d < depth {
                            pending.push(Pending::Children(n, d + 1));
                        }
                    }
                }
            }
            Ok(())
        })();
        if let Err(e) = &outcome
            && element_unavailable(e)
        {
            if let Some(id) = p["cursor"]["cursor_handle"].as_str() {
                self.handles.remove(id);
            }
            return Err(error(
                ErrorType::InvalidParams,
                "UIA traversal anchor was destroyed; reacquire the root and restart traversal",
            ));
        }
        partial(outcome, b)?;
        result["completeness"] = b.completeness();
        let mut new_cursor = None;
        if !pending.is_empty() {
            let id = self.put(
                "ac",
                Handle::Cursor {
                    root: root.clone(),
                    query,
                    view,
                    depth,
                    pending,
                },
            )?;
            result["completeness"]["continuation"] = json!({"root":root,"cursor_handle":id});
            new_cursor = Some(id);
            if result["nodes"].as_array().unwrap().is_empty()
                && b.reason.get() == Some("byte_limit")
            {
                self.handles.remove(string(
                    &result["completeness"]["continuation"],
                    "cursor_handle",
                )?);
                return Err(error(
                    ErrorType::QuotaExceeded,
                    "one UIA node exceeds byte budget",
                ));
            }
        }
        if !b.fits(method, &result)? {
            if let Some(id) = new_cursor {
                self.handles.remove(&id);
            }
            return Err(error(
                ErrorType::QuotaExceeded,
                "UIA page exceeds byte budget",
            ));
        }
        if let Some(id) = p["cursor"]["cursor_handle"].as_str() {
            self.handles.remove(id);
        }
        Ok(result)
    }
}
fn retain_operand_references(value: &Value, retain: &mut impl FnMut(&Value)) {
    match value {
        Value::Object(fields) => {
            retain(value);
            for value in fields.values() {
                retain_operand_references(value, retain);
            }
        }
        Value::Array(items) => {
            for value in items {
                retain_operand_references(value, retain);
            }
        }
        _ => {}
    }
}
fn retained_bytes(handle: &Handle) -> usize {
    // Account for retained registry metadata and native interface slots. The
    // provider owns the remote native allocation, not this helper's heap.
    match handle {
        Handle::Node(_) | Handle::Range { .. } => 1024,
        Handle::Cursor {
            root,
            query,
            pending,
            ..
        } => 1024 + root.to_string().len() + query.to_string().len() + pending.len() * 512,
    }
}
fn element_unavailable(error: &EIPError) -> bool {
    error.data.safe_detail.as_deref() == Some("UIA HRESULT 0x80040201")
}
fn traversal_optional<T>(result: Result<T, EIPError>, _b: &Budget) -> Result<Option<T>, EIPError> {
    match result {
        Err(e) if element_unavailable(&e) => Ok(None),
        result => optional(result),
    }
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}
fn tree_result() -> Value {
    json!({"backend":"uia","sampled_at":now(),"nodes":[],"completeness":{"complete":true}})
}
fn capability(op: &str, source: &str) -> Value {
    json!({"operation":op,"supported":"yes","source":source})
}
fn number(v: &Value, k: &str, default: usize) -> usize {
    v[k].as_u64().map_or(default, |n| n as usize)
}
fn string<'a>(v: &'a Value, k: &str) -> Result<&'a str, EIPError> {
    v[k].as_str().ok_or_else(invalid)
}
fn invalid() -> EIPError {
    error(ErrorType::InvalidParams, "invalid UIA operand")
}
fn stale() -> EIPError {
    error(
        ErrorType::InvalidParams,
        "stale or mismatched UIA reference",
    )
}
fn unsupported() -> EIPError {
    error(
        ErrorType::Unsupported,
        "UIA provider does not expose the requested operation",
    )
}
fn native_error(e: windows::core::Error) -> EIPError {
    let code = e.code();
    let kind = if code.0 as u32 == UIA_E_ELEMENTNOTAVAILABLE {
        ErrorType::InvalidParams
    } else if code.0 as u32 == UIA_E_NOTSUPPORTED
        || code == windows::core::HRESULT(0x80004002u32 as i32)
    {
        ErrorType::Unsupported
    } else if code == windows::core::HRESULT(0x80070005u32 as i32) {
        ErrorType::Denied
    } else {
        ErrorType::ProviderUnavailable
    };
    let mut result = error(kind, "UI Automation native call failed");
    result.data.safe_detail = Some(format!("UIA HRESULT 0x{:08x}", code.0 as u32));
    result
}
fn pattern_error(e: EIPError) -> EIPError {
    // UIA can return success with a null interface for an absent pattern; the
    // generated COM binding represents that null pointer as E_POINTER.
    if e.data.safe_detail.as_deref() == Some("UIA HRESULT 0x80004003") {
        unsupported()
    } else {
        e
    }
}
fn optional<T>(r: Result<T, EIPError>) -> Result<Option<T>, EIPError> {
    match r {
        Ok(v) => Ok(Some(v)),
        Err(e) if e.data.safe_detail.as_deref() == Some("UIA HRESULT 0x80004003") => Ok(None),
        Err(e) => Err(e),
    }
}
fn partial(r: Result<(), EIPError>, b: &Budget) -> Result<(), EIPError> {
    match r {
        Err(_) if matches!(b.reason.get(), Some("deadline" | "call_limit")) => Ok(()),
        r => r,
    }
}
fn field_error(result: &mut Value, field: &str, e: &EIPError) {
    let errors = result["field_errors"].as_array_mut().unwrap();
    if errors.len() < 128 {
        errors.push(json!({"field":field,"reason":if e.data.error_type==ErrorType::Unsupported{"unsupported"}else{"unavailable"}}));
    }
}
fn bounds(r: RECT) -> Value {
    json!({"frame":"uia_physical_screen","x":r.left,"y":r.top,"width":i64::from(r.right)-i64::from(r.left),"height":i64::from(r.bottom)-i64::from(r.top)})
}
fn native_point(p: &Value) -> Result<POINT, EIPError> {
    if p["frame"] != "uia_physical_screen" {
        return Err(invalid());
    }
    let coordinate = |k| {
        p[k].as_f64()
            .filter(|v| v.is_finite() && *v >= i32::MIN as f64 && *v <= i32::MAX as f64)
            .map(|v| v.round() as i32)
            .ok_or_else(invalid)
    };
    Ok(POINT {
        x: coordinate("x")?,
        y: coordinate("y")?,
    })
}
#[allow(
    non_upper_case_globals,
    reason = "Windows SDK constants retain their native names"
)]
fn role_name(id: UIA_CONTROLTYPE_ID) -> Option<&'static str> {
    Some(match id {
        UIA_ButtonControlTypeId => "button",
        UIA_EditControlTypeId => "text",
        UIA_TextControlTypeId => "text",
        UIA_CheckBoxControlTypeId => "checkbox",
        UIA_RadioButtonControlTypeId => "radio",
        UIA_WindowControlTypeId => "window",
        UIA_DocumentControlTypeId => "document",
        UIA_ListControlTypeId => "list",
        UIA_ListItemControlTypeId => "list_item",
        UIA_MenuControlTypeId => "menu",
        UIA_MenuItemControlTypeId => "menu_item",
        UIA_ComboBoxControlTypeId => "combobox",
        UIA_TableControlTypeId => "table",
        UIA_DataGridControlTypeId => "table",
        UIA_TabControlTypeId => "tab_list",
        UIA_TabItemControlTypeId => "tab",
        UIA_SliderControlTypeId => "slider",
        UIA_TreeControlTypeId => "tree",
        UIA_TreeItemControlTypeId => "tree_item",
        UIA_HyperlinkControlTypeId => "link",
        _ => return None,
    })
}
fn matches_query(n: &Value, q: &Value) -> bool {
    if q.is_null() {
        return true;
    }
    for key in ["role", "native_role", "identifier"] {
        if !q[key].is_null() && q[key] != n[key] {
            return false;
        }
    }
    if let Some(name) = q["name"].as_str() {
        let actual = n["name"].as_str().unwrap_or("");
        if match q["name_match"].as_str().unwrap_or("exact") {
            "substring_casefold" => !actual
                .case_fold()
                .collect::<String>()
                .contains(&name.case_fold().collect::<String>()),
            _ => actual != name,
        } {
            return false;
        }
    }
    if let Some(states) = q["states"].as_object() {
        for (k, v) in states {
            if n["states"][k] != *v {
                return false;
            }
        }
    }
    if let Some(op) = q["operation"].as_str()
        && !n["capabilities"]
            .as_array()
            .is_some_and(|a| a.iter().any(|c| c["operation"] == op))
    {
        return false;
    }
    true
}

// Reserve the continuation before accepting a node. The eventual cursor has a
// shorter handle; no result-size error may consume the caller's old cursor.
fn page_fits(method: &str, result: &Value, root: &Value, b: &Budget) -> Result<bool, EIPError> {
    let mut page = result.clone();
    page["completeness"] = json!({"complete":false,"reason":"total_limit","continuation":{"root":root,"cursor_handle":"x".repeat(128)}});
    b.fits(method, &page)
}
fn fit_result(result: &mut Value, method: &str, b: &Budget) -> Result<(), EIPError> {
    while !b.fits(method, result)? {
        result["completeness"] = json!({"complete":false,"reason":"byte_limit"});
        let removed = [
            "nodes",
            "properties",
            "ranges",
            "bounds",
            "attribute_runs",
            "field_errors",
        ]
        .iter()
        .any(|key| {
            result
                .get_mut(*key)
                .and_then(Value::as_array_mut)
                .is_some_and(|items| items.pop().is_some())
        });
        if removed {
            continue;
        }
        if let Some(text) = result["text"]["text"]
            .as_str()
            .filter(|text| !text.is_empty())
        {
            let mut end = text.len() / 2;
            while !text.is_char_boundary(end) {
                end -= 1;
            }
            let retained = text[..end].to_owned();
            result["text"]["status"] = json!(if retained.is_empty() { "empty" } else { "text" });
            result["text"]["text"] = json!(retained);
        } else {
            return Err(error(
                ErrorType::QuotaExceeded,
                "byte budget cannot hold the UIA result envelope",
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "accessibility_windows_tests.rs"]
mod tests;
