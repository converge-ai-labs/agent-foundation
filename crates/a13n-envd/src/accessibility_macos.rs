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
struct Sample {
    value: Value,
    actions: Vec<CFRetained<CFString>>,
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
        let timeout = number(p, "_timeout_ms", number(&p["limits"], "timeout_ms", 5000));
        let reserve = (timeout / 20).min(25);
        Ok(Self {
            limits: p["limits"].clone(),
            calls: Cell::new(0),
            reason: Cell::new(None),
            deadline: Instant::now()
                + Duration::from_millis(timeout.saturating_sub(reserve) as u64),
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
    fn prepare(&self, n: &AXUIElement) -> Result<(), EIPError> {
        self.check()?;
        // This setting applies to this exact AX object, including child objects
        // acquired from arrays. Never leave a native message on the default timeout.
        let seconds = self
            .deadline
            .saturating_duration_since(Instant::now())
            .as_secs_f32();
        if seconds <= 0.0 {
            self.reason.set(Some("deadline"));
            return self.check();
        }
        native::status(unsafe { n.set_messaging_timeout(seconds) })?;
        self.check()
    }
    fn call(&self, n: &AXUIElement, f: impl FnOnce() -> AXError) -> Result<(), EIPError> {
        self.prepare(n)?;
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
        self.prune(p);
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
    fn prune(&mut self, params: &Value) {
        let mut operands = HashSet::new();
        operand_handles(params, &mut operands);
        let mut keep = operands.clone();
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
        // An expired operand can retain its owner until this executing call has
        // validated it. Expiry never replaces the object behind an old handle.
        for id in &operands {
            if let Some(e) = self.handles.get(id) {
                handle_dependencies(&e.value, &mut keep);
            }
        }
        self.handles
            .retain(|id, e| e.touched.elapsed() < Duration::from_secs(120) || keep.contains(id));
        // Evict owned ranges/actions/cursors first, then recompute which nodes
        // their remaining references pin. Nothing is evicted during a request.
        for kind in 0..4 {
            let dependencies = if kind == 3 {
                let mut deps = operands.clone();
                for entry in self.handles.values() {
                    handle_dependencies(&entry.value, &mut deps);
                }
                deps
            } else {
                operands.clone()
            };
            let mut candidates: Vec<_> = self
                .handles
                .iter()
                .filter(|(id, e)| handle_kind(&e.value) == kind && !dependencies.contains(*id))
                .map(|(id, e)| (id.clone(), e.touched))
                .collect();
            candidates.sort_unstable_by_key(|(_, touched)| *touched);
            let mut count = self
                .handles
                .values()
                .filter(|e| handle_kind(&e.value) == kind)
                .count();
            let mut bytes: usize = self
                .handles
                .values()
                .map(|e| retained_bytes(&e.value))
                .sum();
            for (id, _) in candidates {
                if count < handle_capacity(kind) / 2
                    && self.handles.len() < 2048
                    && bytes < 4 * 1024 * 1024
                {
                    break;
                }
                if let Some(entry) = self.handles.remove(&id) {
                    count -= 1;
                    bytes = bytes.saturating_sub(retained_bytes(&entry.value));
                }
            }
        }
    }
    fn put(&mut self, prefix: &str, value: Handle) -> Result<String, EIPError> {
        let count = self
            .handles
            .values()
            .filter(|e| std::mem::discriminant(&e.value) == std::mem::discriminant(&value))
            .count();
        let max = handle_capacity(handle_kind(&value));
        let bytes: usize = self
            .handles
            .values()
            .map(|e| retained_bytes(&e.value))
            .sum();
        if self.handles.len() >= 4096
            || count >= max
            || bytes.saturating_add(retained_bytes(&value)) > 8 * 1024 * 1024
        {
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
        let sample = self.sample_fields(n, b)?;
        self.publish_sample(n, sample)
    }
    fn sample_fields(&self, n: &AXUIElement, b: &Budget) -> Result<Sample, EIPError> {
        // Reserve the maximum wire handle length, without registering unmatched
        // nodes or their actions. Publication happens after query and byte fitting.
        let r = json!({"backend":"ax","backend_epoch":self.epoch,"node_handle":"x".repeat(128)});
        let role = native::identifier(&*get(n, "AXRole", b)?)?;
        let mut action_names = Vec::new();
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
            b.check()?;
            return Ok(Sample {
                value: out,
                actions: action_names,
            });
        }
        match native::names(n, "actions", b) {
            Ok(actions) => {
                if actions.len() > b.limit("max_items").min(64) {
                    b.mark("item_limit");
                }
                for i in 0..actions.len().min(b.limit("max_items")).min(64) {
                    let name = typed::<CFString>(actions.get(i).ok_or_else(unavailable)?)?;
                    let identifier = native::identifier(&name)?;
                    let id = "x".repeat(128);
                    action_names.push(name);
                    out["capabilities"].as_array_mut().unwrap().push(json!({"operation":"perform_action","supported":"yes","source":"AXAction","action":{"node":r,"action_handle":id,"native_identifier":identifier}}));
                }
            }
            Err(e) => field_error(&mut out, "actions", &e),
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
        b.check()?;
        Ok(Sample {
            value: out,
            actions: action_names,
        })
    }
    fn publish_sample(
        &mut self,
        n: &CFRetained<AXUIElement>,
        sample: Sample,
    ) -> Result<Value, EIPError> {
        let mut out = sample.value;
        let reference = self.reference(n.clone())?;
        out["reference"] = reference.clone();
        let owner = string(&reference, "node_handle")?.to_owned();
        let mut names = sample.actions.into_iter();
        for capability in out["capabilities"].as_array_mut().unwrap() {
            if capability["operation"] != "perform_action" {
                continue;
            }
            let name = names.next().ok_or_else(unavailable)?;
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
                None => self.put(
                    "aa",
                    Handle::Action {
                        owner: owner.clone(),
                        name,
                    },
                )?,
            };
            capability["action"]["node"] = reference.clone();
            capability["action"]["action_handle"] = json!(id);
        }
        Ok(out)
    }
    fn applications(&self, b: &Budget) -> Result<Vec<CFRetained<AXUIElement>>, EIPError> {
        use objc2_app_kit::{NSApplicationActivationPolicy, NSWorkspace};
        use objc2_core_foundation::{CFRunLoop, kCFRunLoopDefaultMode};
        b.check()?;
        // NSWorkspace refreshes its runningApplications cache through the main
        // run loop. Pump pending notifications between serialized requests.
        unsafe {
            CFRunLoop::run_in_mode(kCFRunLoopDefaultMode, 0.0, false);
        }
        let apps = NSWorkspace::sharedWorkspace().runningApplications();
        // Default discovery targets regular GUI applications. Accessory/background
        // applications remain explicitly addressable through application scope.
        Ok(apps
            .iter()
            .filter(|app| app.activationPolicy() == NSApplicationActivationPolicy::Regular)
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
                    let Some(total) = optional(native::count(&n, "AXWindows", b))? else {
                        continue;
                    };
                    for i in 0..total {
                        if out["nodes"].as_array().unwrap().len() >= b.limit("max_nodes") {
                            b.mark("node_limit");
                            break;
                        }
                        let page = native::page(&n, "AXWindows", i, 1, b)?;
                        if let Some(value) = page.get(0) {
                            let node = self.sample(&typed(value)?, b)?;
                            out["nodes"].as_array_mut().unwrap().push(node);
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
        let root = p["node"].clone();
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
                        let mut sample = self.sample_fields(&n, b)?;
                        if let Some(parent) = parent {
                            sample.value["parent"] = parent;
                        }
                        let matched = matches_query(&sample.value, &query);
                        let mut reference = Value::Null;
                        if matched {
                            out["nodes"]
                                .as_array_mut()
                                .unwrap()
                                .push(sample.value.clone());
                            if !page_fits(method, &out, &root, b)? {
                                out["nodes"].as_array_mut().unwrap().pop();
                                b.mark("byte_limit");
                                break;
                            }
                            let node = self.publish_sample(&n, sample)?;
                            reference = node["reference"].clone();
                            *out["nodes"].as_array_mut().unwrap().last_mut().unwrap() = node;
                        }
                        pending.pop();
                        visited += 1;
                        if d < depth {
                            if !matched {
                                reference = self.reference(n.clone())?;
                            }
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
            ensure_progress(visited, b)?;
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
fn handle_kind(value: &Handle) -> usize {
    match value {
        Handle::Action { .. } => 0,
        Handle::Range { .. } => 1,
        Handle::Cursor { .. } => 2,
        Handle::Node(_) => 3,
    }
}
fn handle_capacity(kind: usize) -> usize {
    [512, 256, 64, 4096][kind]
}
fn operand_handles(value: &Value, handles: &mut HashSet<String>) {
    match value {
        Value::Object(fields) => {
            for (key, value) in fields {
                if matches!(
                    key.as_str(),
                    "node_handle" | "range_handle" | "cursor_handle" | "action_handle"
                ) {
                    if let Some(id) = value.as_str() {
                        handles.insert(id.to_owned());
                    }
                } else {
                    operand_handles(value, handles);
                }
            }
        }
        Value::Array(values) => {
            for value in values {
                operand_handles(value, handles);
            }
        }
        _ => {}
    }
}
fn handle_dependencies(value: &Handle, keep: &mut HashSet<String>) {
    match value {
        Handle::Action { owner, .. } | Handle::Range { owner, .. } => {
            keep.insert(owner.clone());
        }
        Handle::Cursor { root, pending, .. } => {
            operand_handles(root, keep);
            for stage in pending {
                match stage {
                    Pending::Node(_, _, Some(parent)) | Pending::Children(_, _, parent, _) => {
                        operand_handles(parent, keep)
                    }
                    _ => {}
                }
            }
        }
        Handle::Node(_) => {}
    }
}
fn retained_bytes(value: &Handle) -> usize {
    512 + match value {
        Handle::Action { owner, name } => owner.len() + name.length() as usize * 2,
        Handle::Range { owner, .. } => owner.len(),
        Handle::Cursor {
            root,
            query,
            view,
            pending,
            ..
        } => root.to_string().len() + query.to_string().len() + view.len() + pending.len() * 512,
        Handle::Node(_) => 0,
    }
}
fn collection_room(out: &Value, key: &str, b: &Budget) -> bool {
    if out[key]
        .as_array()
        .is_some_and(|v| v.len() >= b.limit("max_items"))
    {
        b.mark("item_limit");
        false
    } else {
        true
    }
}
fn ensure_progress(visited: usize, b: &Budget) -> Result<(), EIPError> {
    if visited == 0 {
        b.check()?;
        return Err(error(
            ErrorType::QuotaExceeded,
            "AX page cannot complete one node within its budget",
        ));
    }
    Ok(())
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
        b.mark("byte_limit");
        out["completeness"] = b.completeness();
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
