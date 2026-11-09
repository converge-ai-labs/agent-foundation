//! Session-helper-local AX objects, native ranges, actions and traversal cursors.
//! The helper's initial thread owns AppKit and CF objects for their full lifetime.
#![allow(
    unsafe_code,
    reason = "ApplicationServices exposes unsafe CF ownership and AX calls"
)]
use super::{Backend, EIPError, ErrorType, error};
use objc2_application_services::{AXError, AXIsProcessTrusted, AXUIElement, AXValue, AXValueType};
use objc2_core_foundation::{
    CFNumber, CFRange, CFRetained, CFString, CFType, CGPoint, CGRect, CGSize,
};
use serde_json::{Value, json};
use std::{
    cell::Cell,
    collections::{HashMap, HashSet},
    ptr::NonNull,
    time::{Duration, Instant},
};
use unicode_casefold::UnicodeCaseFold;
#[path = "accessibility_macos_mutation.rs"]
mod mutation;
#[path = "accessibility_macos_native.rs"]
mod native;
#[path = "accessibility_macos_range.rs"]
mod range;
#[path = "accessibility_macos_read.rs"]
mod read;
use native::{get, optional, typed};
// AX arrays contain CF objects; individual element types remain heterogeneous.
type CFArray = objc2_core_foundation::CFArray<CFType>;

pub(super) const METHODS: &[&str] = &[
    "computer.a11y_describe",
    "computer.a11y_roots",
    "computer.a11y_inspect",
    "computer.a11y_find",
    "computer.a11y_read",
    "computer.a11y_range",
    "computer.a11y_range_read",
    "computer.ax_perform_action",
    "computer.ax_focus",
    "computer.ax_focused_window_set",
    "computer.ax_main_set",
    "computer.ax_minimized_set",
    "computer.ax_expanded_set",
    "computer.ax_disclosing_set",
    "computer.ax_selected_set",
    "computer.ax_selected_children_set",
    "computer.ax_selected_rows_set",
    "computer.ax_selected_columns_set",
    "computer.ax_position_set",
    "computer.ax_size_set",
    "computer.ax_value_string_set",
    "computer.ax_value_number_set",
    "computer.ax_selected_text_set",
    "computer.ax_text_range_select",
    "computer.ax_text_ranges_select",
    "computer.ax_visible_text_range_set",
];
pub(super) fn new(epoch: &str) -> Result<Box<dyn Backend>, EIPError> {
    Ok(Box::new(Native {
        epoch: epoch.into(),
        next: 0,
        handles: HashMap::new(),
    }))
}
struct Native {
    epoch: String,
    next: u64,
    handles: HashMap<String, Entry>,
}
struct Entry {
    value: Handle,
    touched: Instant,
}
#[derive(Clone)]
enum Handle {
    Node(CFRetained<AXUIElement>),
    Action {
        owner: String,
        name: CFRetained<CFString>,
    },
    Range {
        owner: String,
        span: CFRange,
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
    Node(CFRetained<AXUIElement>, usize, Option<Value>),
    Children(CFRetained<AXUIElement>, usize, Value, usize),
}
struct Budget {
    limits: Value,
    calls: Cell<usize>,
    reason: Cell<Option<&'static str>>,
    deadline: Instant,
    projection: usize,
}
impl Budget {
    fn new(p: &Value) -> Result<Self, EIPError> {
        Ok(Self {
            limits: p["limits"].clone(),
            calls: Cell::new(0),
            reason: Cell::new(None),
            deadline: Instant::now()
                + Duration::from_millis(number(&p["limits"], "timeout_ms", 5000) as u64),
            // The Session replaces the public observation ID with this reserve.
            projection: number(p, "_projection_bytes", 0),
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
        if self.reason.get().is_none() {
            self.reason.set(Some(reason));
        }
    }
    fn check(&self) -> Result<(), EIPError> {
        if Instant::now() >= self.deadline {
            self.reason.set(Some("deadline"));
        }
        if self.calls.get() >= self.limit("max_native_calls") {
            self.reason.set(Some("call_limit"));
        }
        if self.exhausted() {
            Err(error(ErrorType::Timeout, "AX native budget exhausted"))
        } else {
            Ok(())
        }
    }
    fn exhausted(&self) -> bool {
        matches!(self.reason.get(), Some("deadline" | "call_limit"))
    }
    fn call(&self, f: impl FnOnce() -> AXError) -> Result<(), EIPError> {
        self.check()?;
        self.calls.set(self.calls.get() + 1);
        native::status(f())
    }
    fn completeness(&self) -> Value {
        match self.reason.get() {
            Some(r) => json!({"complete":false,"reason":r}),
            None => json!({"complete":true}),
        }
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
        objc2::rc::autoreleasepool(|_| {
            // Foreign exceptions leave the helper protocol with explicit failure;
            // the supervisor retains uncertain evidence if dispatch already ran.
            {
                objc2::exception::catch(std::panic::AssertUnwindSafe(|| {
                    self.execute_inner(method, &params, dispatch)
                }))
            }
            .map_err(|_| unavailable())?
        })
    }
}
impl Native {
    fn execute_inner(
        &mut self,
        method: &str,
        p: &Value,
        dispatch: &mut dyn FnMut() -> Result<(), EIPError>,
    ) -> Result<Value, EIPError> {
        self.prune();
        let b = Budget::new(p)?;
        let trusted = unsafe { AXIsProcessTrusted() };
        if method == "computer.a11y_describe" {
            return Ok(
                json!({"backend":"ax","backend_epoch":self.epoch,"ready":trusted,"reason":if trusted{"ready"}else{"permission_required"},"desktop_identity":"inherited macOS login session","methods":METHODS,"frames":["ax_screen"],"limits":{}}),
            );
        }
        if !trusted {
            return Err(error(
                ErrorType::Denied,
                "grant Accessibility permission to envd in macOS System Settings",
            ));
        }
        let mut result = match method {
            "computer.a11y_roots" => self.roots(p, &b),
            "computer.a11y_inspect" | "computer.a11y_find" => self.tree(method, p, &b),
            "computer.a11y_read" => self.read(p, &b),
            "computer.a11y_range" => self.range(p, &b),
            "computer.a11y_range_read" => self.range_read(p, &b),
            _ => return self.mutate(method, p, &b, dispatch),
        }?;
        if !matches!(method, "computer.a11y_inspect" | "computer.a11y_find") {
            fit_result(&mut result, method, &b)?;
        }
        Ok(result)
    }
    fn prune(&mut self) {
        let mut keep = HashSet::new();
        for entry in self
            .handles
            .values()
            .filter(|e| e.touched.elapsed() < Duration::from_secs(120))
        {
            match &entry.value {
                Handle::Action { owner, .. } | Handle::Range { owner, .. } => {
                    keep.insert(owner.clone());
                }
                Handle::Cursor { root, pending, .. } => {
                    if let Some(id) = root["node_handle"].as_str() {
                        keep.insert(id.to_owned());
                    }
                    for p in pending {
                        let parent = match p {
                            Pending::Node(_, _, p) => p.as_ref(),
                            Pending::Children(_, _, p, _) => Some(p),
                        };
                        if let Some(id) = parent.and_then(|p| p["node_handle"].as_str()) {
                            keep.insert(id.to_owned());
                        }
                    }
                }
                Handle::Node(_) => {}
            }
        }
        self.handles
            .retain(|id, e| e.touched.elapsed() < Duration::from_secs(120) || keep.contains(id));
    }
    fn put(&mut self, prefix: &str, value: Handle) -> Result<String, EIPError> {
        let count = self
            .handles
            .values()
            .filter(|e| std::mem::discriminant(&e.value) == std::mem::discriminant(&value))
            .count();
        let max = match value {
            Handle::Range { .. } => 256,
            Handle::Action { .. } => 512,
            Handle::Cursor { .. } => 64,
            Handle::Node(_) => 4096,
        };
        if self.handles.len() >= 4096 || count >= max {
            return Err(error(
                ErrorType::QuotaExceeded,
                "AX handle registry is full",
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
    fn reference(&mut self, n: CFRetained<AXUIElement>) -> Result<Value, EIPError> {
        let existing = self.handles.iter().find_map(|(id, e)| match &e.value {
            Handle::Node(x) if x == &n => Some(id.clone()),
            _ => None,
        });
        let id = match existing {
            Some(id) => {
                self.handles.get_mut(&id).unwrap().touched = Instant::now();
                id
            }
            None => self.put("an", Handle::Node(n))?,
        };
        Ok(json!({"backend":"ax","backend_epoch":self.epoch,"node_handle":id}))
    }
    fn node(&mut self, r: &Value) -> Result<CFRetained<AXUIElement>, EIPError> {
        if r["backend"] != "ax" || r["backend_epoch"] != self.epoch {
            return Err(stale());
        }
        let entry = self
            .handles
            .get_mut(string(r, "node_handle")?)
            .ok_or_else(stale)?;
        entry.touched = Instant::now();
        match &entry.value {
            Handle::Node(n) => Ok(n.clone()),
            _ => Err(stale()),
        }
    }
    fn protected(&self, n: &AXUIElement, b: &Budget) -> Result<bool, EIPError> {
        if optional(get(n, "AXSubrole", b))?
            .map(|v| native::string_is(&v, "AXSecureTextField"))
            .transpose()?
            == Some(true)
        {
            return Ok(true);
        }
        Ok(optional(get(n, "AXContainsProtectedContent", b))?
            .map(|v| native::boolean(&v))
            .transpose()?
            .unwrap_or(false))
    }
    fn sample(&mut self, n: &CFRetained<AXUIElement>, b: &Budget) -> Result<Value, EIPError> {
        let r = self.reference(n.clone())?;
        let role = native::name(&*get(n, "AXRole", b)?, b)?;
        let mut out = json!({"reference":r,"native_role":role,"role":role_name(&role),"sampled_at":now(),"states":{},"capabilities":[],"field_errors":[]});
        for (field, key) in [
            ("name", "AXTitle"),
            ("description", "AXDescription"),
            ("identifier", "AXIdentifier"),
        ] {
            match optional(get(n, key, b)).and_then(|v| v.map(|v| native::name(&v, b)).transpose())
            {
                Ok(Some(v)) => out[field] = json!(v),
                Ok(None) => {}
                Err(e) => field_error(&mut out, field, &e),
            }
        }
        for (field, key) in [
            ("enabled", "AXEnabled"),
            ("focused", "AXFocused"),
            ("selected", "AXSelected"),
            ("expanded", "AXExpanded"),
            ("minimized", "AXMinimized"),
            ("main", "AXMain"),
            ("disclosing", "AXDisclosing"),
        ] {
            match optional(get(n, key, b)).and_then(|v| v.map(|v| native::boolean(&v)).transpose())
            {
                Ok(Some(v)) => out["states"][field] = json!(v),
                Ok(None) => {}
                Err(e) => field_error(&mut out, field, &e),
            }
        }
        let protected = self.protected(n, b);
        match &protected {
            Ok(v) => out["states"]["protected"] = json!(v),
            Err(e) => field_error(&mut out, "protected", e),
        }
        let bounds = (|| {
            let p = native::point(&*get(n, "AXPosition", b)?)?;
            let s = native::size(&*get(n, "AXSize", b)?)?;
            rect_value(CGRect { origin: p, size: s })
        })();
        match bounds {
            Ok(v) => out["bounds"] = v,
            Err(e) => field_error(&mut out, "bounds", &e),
        }
        match native::count(n, "AXChildren", b) {
            Ok(v) => out["child_count"] = json!(v),
            Err(e) => field_error(&mut out, "child_count", &e),
        }
        if out["states"]["enabled"] == false {
            return Ok(out);
        }
        if let Ok(actions) = native::names(n, "actions", b) {
            if actions.len() > b.limit("max_items").min(64) {
                b.mark("item_limit");
            }
            for i in 0..actions.len().min(b.limit("max_items")).min(64) {
                let name = typed::<CFString>(actions.get(i).ok_or_else(unavailable)?)?;
                let identifier = native::name(&name, b)?;
                let owner = string(&r, "node_handle")?.to_owned();
                let existing = self.handles.iter().find_map(|(id, e)| match &e.value {
                    Handle::Action { owner: o, name: x } if o == &owner && x == &name => {
                        Some(id.clone())
                    }
                    _ => None,
                });
                let id = match existing {
                    Some(id) => {
                        self.handles.get_mut(&id).unwrap().touched = Instant::now();
                        id
                    }
                    None => self.put("aa", Handle::Action { owner, name })?,
                };
                out["capabilities"].as_array_mut().unwrap().push(json!({"operation":"perform_action","supported":"yes","source":"AXAction","action":{"node":r,"action_handle":id,"native_identifier":identifier}}));
            }
        }
        let mut observed_value = None;
        for &(op, key) in mutation::SETTERS {
            if protected.as_ref().ok() != Some(&false) && mutation::content(op) {
                continue;
            }
            match native::writable(n, key, b) {
                Ok(true) => {
                    if key == "AXValue" {
                        match observed_value.get_or_insert_with(|| get(n, key, b)) {
                            Ok(value) if mutation::value_type_matches(value, op) => {}
                            Ok(_) => continue,
                            Err(e) => {
                                field_error(&mut out, key, e);
                                continue;
                            }
                        }
                    }
                    out["capabilities"]
                        .as_array_mut()
                        .unwrap()
                        .push(json!({"operation":op,"supported":"yes","source":key}));
                }
                Ok(false) => {}
                Err(e) if e.data.error_type == ErrorType::Unsupported => {}
                Err(e) => field_error(&mut out, key, &e),
            }
            if b.exhausted() {
                break;
            }
        }
        Ok(out)
    }
    fn applications(&self, b: &Budget) -> Result<Vec<CFRetained<AXUIElement>>, EIPError> {
        use objc2_app_kit::NSWorkspace;
        use objc2_core_foundation::{CFRunLoop, kCFRunLoopDefaultMode};
        b.check()?;
        // NSWorkspace refreshes its runningApplications cache through the main
        // run loop. Pump pending notifications between serialized requests.
        unsafe {
            CFRunLoop::run_in_mode(kCFRunLoopDefaultMode, 0.0, false);
        }
        let apps = NSWorkspace::sharedWorkspace().runningApplications();
        let max = b.limit("max_nodes");
        if apps.len() > max {
            b.mark("node_limit");
        }
        Ok(apps
            .iter()
            .take(max)
            .map(|app| unsafe { AXUIElement::new_application(app.processIdentifier()) })
            .collect())
    }
    fn roots(&mut self, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let scope = &p["scope"];
        let mut out = tree_result();
        let result = (|| {
            let system = unsafe { AXUIElement::new_system_wide() };
            let roots = match scope["kind"].as_str().unwrap_or("windows") {
                "desktop" => vec![system],
                "at_point" => vec![native::hit(&system, native_point(&scope["point"])?, b)?],
                "focused" => vec![typed(get(&system, "AXFocusedUIElement", b)?)?],
                "application" => {
                    let pid = scope["process_id"]
                        .as_i64()
                        .and_then(|v| i32::try_from(v).ok())
                        .filter(|v| *v > 0)
                        .ok_or_else(invalid)?;
                    vec![unsafe { AXUIElement::new_application(pid) }]
                }
                "applications" | "windows" => self.applications(b)?,
                _ => return Err(unsupported()),
            };
            for n in roots {
                if scope["kind"] == "windows" || scope["kind"].is_null() {
                    let total = native::count(&n, "AXWindows", b)?;
                    for i in 0..total {
                        if out["nodes"].as_array().unwrap().len() >= b.limit("max_nodes") {
                            b.mark("node_limit");
                            break;
                        }
                        let page = native::page(&n, "AXWindows", i, 1, b)?;
                        if let Some(v) = page.get(0) {
                            let n = typed(v)?;
                            out["nodes"]
                                .as_array_mut()
                                .unwrap()
                                .push(self.sample(&n, b)?);
                        }
                    }
                } else {
                    out["nodes"]
                        .as_array_mut()
                        .unwrap()
                        .push(self.sample(&n, b)?);
                }
                if out["nodes"].as_array().unwrap().len() >= b.limit("max_nodes") {
                    break;
                }
            }
            Ok(())
        })();
        partial(result, b)?;
        out["completeness"] = b.completeness();
        Ok(out)
    }
    fn tree(&mut self, method: &str, p: &Value, b: &Budget) -> Result<Value, EIPError> {
        let root = p["root"].clone();
        let node = self.node(&root)?;
        let query = p["query"].clone();
        let view = p["view"].as_str().unwrap_or("control").to_owned();
        let depth = b.limit("depth");
        let mut pending = if let Some(id) = p["cursor"]["cursor_handle"].as_str() {
            if p["cursor"]["root"] != root {
                return Err(stale());
            }
            let e = self.handles.get_mut(id).ok_or_else(stale)?;
            e.touched = Instant::now();
            match &e.value {
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
            vec![Pending::Node(node, 0, None)]
        };
        let mut out = tree_result();
        let mut visited = 0;
        let result = (|| {
            while let Some(stage) = pending.last().cloned() {
                b.check()?;
                if visited >= b.limit("max_nodes") {
                    b.mark("node_limit");
                    break;
                }
                if method == "computer.a11y_find"
                    && out["nodes"].as_array().unwrap().len() >= b.limit("max_matches")
                {
                    b.mark("total_limit");
                    break;
                }
                match stage {
                    Pending::Children(n, d, parent, index) => {
                        let total = native::count(&n, "AXChildren", b)?;
                        if index >= total {
                            pending.pop();
                            continue;
                        }
                        let page = native::page(&n, "AXChildren", index, 1, b)?;
                        let child = page.get(0).map(typed::<AXUIElement>).transpose()?;
                        pending.pop();
                        pending.push(Pending::Children(n, d, parent.clone(), index + 1));
                        if let Some(n) = child {
                            pending.push(Pending::Node(n, d, Some(parent)));
                        }
                    }
                    Pending::Node(n, d, parent) => {
                        let mut sample = self.sample(&n, b)?;
                        if let Some(parent) = parent {
                            sample["parent"] = parent;
                        }
                        let reference = sample["reference"].clone();
                        if matches_query(&sample, &query) {
                            out["nodes"].as_array_mut().unwrap().push(sample);
                            if !page_fits(method, &out, &root, b)? {
                                out["nodes"].as_array_mut().unwrap().pop();
                                b.mark("byte_limit");
                                break;
                            }
                        }
                        pending.pop();
                        visited += 1;
                        if d < depth {
                            pending.push(Pending::Children(n, d + 1, reference, 0));
                        }
                    }
                }
            }
            Ok(())
        })();
        partial(result, b)?;
        out["completeness"] = b.completeness();
        let mut cursor = None;
        if !pending.is_empty() {
            if out["nodes"].as_array().unwrap().is_empty() && b.reason.get() == Some("byte_limit") {
                return Err(error(
                    ErrorType::QuotaExceeded,
                    "one AX node exceeds byte budget",
                ));
            }
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
            out["completeness"]["continuation"] = json!({"root":root,"cursor_handle":id});
            cursor = Some(id);
        }
        if !b.fits(method, &out)? {
            if let Some(id) = cursor {
                self.handles.remove(&id);
            }
            return Err(error(
                ErrorType::QuotaExceeded,
                "AX page exceeds byte budget",
            ));
        }
        if let Some(id) = p["cursor"]["cursor_handle"].as_str() {
            self.handles.remove(id);
        }
        Ok(out)
    }
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}
fn tree_result() -> Value {
    json!({"backend":"ax","sampled_at":now(),"nodes":[],"completeness":{"complete":true}})
}
fn number(v: &Value, k: &str, default: usize) -> usize {
    v[k].as_u64().map_or(default, |n| n as usize)
}
fn string<'a>(v: &'a Value, k: &str) -> Result<&'a str, EIPError> {
    v[k].as_str().ok_or_else(invalid)
}
fn invalid() -> EIPError {
    error(ErrorType::InvalidParams, "invalid AX operand")
}
fn stale() -> EIPError {
    error(ErrorType::InvalidParams, "stale or mismatched AX reference")
}
fn unsupported() -> EIPError {
    error(
        ErrorType::Unsupported,
        "AX provider does not expose the requested operation",
    )
}
fn unavailable() -> EIPError {
    error(
        ErrorType::ProviderUnavailable,
        "AX value is unavailable or has an unexpected native type",
    )
}
fn partial(r: Result<(), EIPError>, b: &Budget) -> Result<(), EIPError> {
    match r {
        Err(_) if b.exhausted() => Ok(()),
        r => r,
    }
}
fn field_error(out: &mut Value, field: &str, e: &EIPError) {
    let errors = out["field_errors"].as_array_mut().unwrap();
    if errors.len() >= 128 {
        return;
    }
    let reason = match e.data.error_type {
        ErrorType::Unsupported => "unsupported",
        ErrorType::Denied => "denied",
        ErrorType::Timeout => "timeout",
        _ if native::code(e) == Some(-25202) => "stale",
        _ => "unavailable",
    };
    errors.push(json!({"field":field,"reason":reason,"native_code":native::code(e)}));
}
fn rect_value(r: CGRect) -> Result<Value, EIPError> {
    if [r.origin.x, r.origin.y, r.size.width, r.size.height]
        .iter()
        .any(|v| !v.is_finite())
        || r.size.width < 0.0
        || r.size.height < 0.0
    {
        return Err(unavailable());
    }
    Ok(
        json!({"frame":"ax_screen","x":r.origin.x,"y":r.origin.y,"width":r.size.width,"height":r.size.height}),
    )
}
fn native_point(p: &Value) -> Result<CGPoint, EIPError> {
    if p["frame"] != "ax_screen" {
        return Err(invalid());
    }
    Ok(CGPoint {
        x: finite(p, "x")?,
        y: finite(p, "y")?,
    })
}
fn finite(p: &Value, k: &str) -> Result<f64, EIPError> {
    p[k].as_f64().filter(|v| v.is_finite()).ok_or_else(invalid)
}
fn role_name(r: &str) -> Option<&'static str> {
    Some(match r {
        "AXApplication" => "application",
        "AXWindow" => "window",
        "AXButton" => "button",
        "AXCheckBox" => "checkbox",
        "AXRadioButton" => "radio",
        "AXTextField" | "AXTextArea" | "AXStaticText" => "text",
        "AXLink" => "link",
        "AXList" => "list",
        "AXMenu" => "menu",
        "AXMenuItem" => "menu_item",
        "AXPopUpButton" | "AXComboBox" => "combobox",
        "AXTable" => "table",
        "AXRow" => "row",
        "AXColumn" => "column",
        "AXCell" => "cell",
        "AXSlider" => "slider",
        "AXOutline" => "tree",
        "AXTabGroup" => "tab_list",
        _ => return None,
    })
}
fn matches_query(n: &Value, q: &Value) -> bool {
    for k in ["role", "native_role", "identifier"] {
        if !q[k].is_null() && q[k] != n[k] {
            return false;
        }
    }
    if let Some(want) = q["name"].as_str() {
        let actual = n["name"].as_str().unwrap_or("");
        if if q["name_match"] == "substring_casefold" {
            !actual
                .case_fold()
                .collect::<String>()
                .contains(&want.case_fold().collect::<String>())
        } else {
            actual != want
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
    if let Some(op) = q["operation"].as_str() {
        return n["capabilities"]
            .as_array()
            .is_some_and(|v| v.iter().any(|v| v["operation"] == op));
    }
    true
}
fn page_fits(method: &str, result: &Value, root: &Value, b: &Budget) -> Result<bool, EIPError> {
    let mut page = result.clone();
    page["completeness"] = json!({"complete":false,"reason":"total_limit","continuation":{"root":root,"cursor_handle":"x".repeat(128)}});
    b.fits(method, &page)
}
fn fit_result(out: &mut Value, method: &str, b: &Budget) -> Result<(), EIPError> {
    while !b.fits(method, out)? {
        out["completeness"] = json!({"complete":false,"reason":"byte_limit"});
        if [
            "nodes",
            "properties",
            "ranges",
            "bounds",
            "attribute_runs",
            "attributes",
            "field_errors",
        ]
        .iter()
        .any(|k| {
            out.get_mut(*k)
                .and_then(Value::as_array_mut)
                .is_some_and(|v| v.pop().is_some())
        }) {
            continue;
        }
        if let Some(text) = out["text"]["text"].as_str().filter(|s| !s.is_empty()) {
            let mut end = text.len() / 2;
            while !text.is_char_boundary(end) {
                end -= 1;
            }
            let text = text[..end].to_owned();
            let consumed = text.encode_utf16().count();
            if let Some(start) = out["text"]["span"]["start"].as_u64() {
                out["text"]["span"]["end"] = json!(start + consumed as u64);
            }
            out["text"]["status"] = json!(if text.is_empty() { "empty" } else { "text" });
            out["text"]["text"] = json!(text);
        } else {
            return Err(error(
                ErrorType::QuotaExceeded,
                "byte budget cannot hold the AX result envelope",
            ));
        }
    }
    Ok(())
}
#[cfg(test)]
#[path = "accessibility_macos_tests.rs"]
mod tests;
