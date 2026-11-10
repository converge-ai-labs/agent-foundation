//! AT-SPI objects stay on the inherited desktop's accessibility bus. The parent
//! owns deadlines and receipts; this process owns native identities and spans.
use super::{Backend, EIPError, ErrorType, error};
use atspi_proxies::{
    self as atspi,
    common::{CoordType, Interface, ObjectRef, ObjectRefOwned, Role, ScrollType, State},
};
use serde_json::{Value, json};
use std::{
    cell::Cell,
    collections::{HashMap, HashSet},
    future::Future,
    time::{Duration, Instant},
};
use unicode_casefold::UnicodeCaseFold;
use zbus::{Connection, proxy::CacheProperties};

#[path = "accessibility_linux_mutation.rs"]
mod mutation;
#[path = "accessibility_linux_range.rs"]
mod range;
#[path = "accessibility_linux_read.rs"]
mod read;

pub(super) const METHODS: &[&str] = &[
    "computer.a11y_describe",
    "computer.a11y_roots",
    "computer.a11y_inspect",
    "computer.a11y_find",
    "computer.a11y_read",
    "computer.a11y_range",
    "computer.a11y_range_read",
    "computer.atspi_do_action",
    "computer.atspi_focus",
    "computer.atspi_value_set",
    "computer.atspi_selection_add_child",
    "computer.atspi_selection_remove_child",
    "computer.atspi_selection_remove_selected",
    "computer.atspi_selection_all",
    "computer.atspi_selection_clear",
    "computer.atspi_text_contents_set",
    "computer.atspi_text_insert",
    "computer.atspi_text_delete",
    "computer.atspi_text_copy",
    "computer.atspi_text_cut",
    "computer.atspi_text_paste",
    "computer.atspi_caret_set",
    "computer.atspi_text_selection_add",
    "computer.atspi_text_selection_set",
    "computer.atspi_text_selection_remove",
    "computer.atspi_document_selections_set",
    "computer.atspi_scroll_to",
    "computer.atspi_scroll_to_point",
    "computer.atspi_range_scroll_to",
    "computer.atspi_range_scroll_to_point",
    "computer.atspi_component_position_set",
    "computer.atspi_component_size_set",
    "computer.atspi_component_extents_set",
    "computer.atspi_table_row_select",
    "computer.atspi_table_row_unselect",
    "computer.atspi_table_column_select",
    "computer.atspi_table_column_unselect",
];
#[cfg(test)]
#[path = "accessibility_linux_tests.rs"]
mod tests;

const ROOT: &str = "/org/a11y/atspi/accessible/root";
const REGISTRY: &str = "org.a11y.atspi.Registry";
const MAX_HANDLES: usize = 16384;
const MAX_REGISTRY_BYTES: usize = 16 * 1024 * 1024;
const MAX_ACTION_BYTES: usize = 64 * 1024;

pub(super) fn new(epoch: &str) -> Result<Box<dyn Backend>, EIPError> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| {
            error(
                ErrorType::InternalError,
                "cannot create accessibility runtime",
            )
        })?;
    Ok(Box::new(Linux {
        runtime,
        native: Native {
            epoch: epoch.into(),
            connection: None,
            registry: Registry::default(),
        },
    }))
}
struct Linux {
    runtime: tokio::runtime::Runtime,
    native: Native,
}
impl Backend for Linux {
    fn execute(
        &mut self,
        method: &str,
        params: Value,
        dispatch: &mut dyn FnMut() -> Result<(), EIPError>,
    ) -> Result<Value, EIPError> {
        self.runtime
            .block_on(self.native.execute(method, params, dispatch))
    }
}

struct Native {
    epoch: String,
    connection: Option<Connection>,
    registry: Registry,
}
#[derive(Clone)]
enum Handle {
    Node(ObjectRefOwned),
    Range {
        node: ObjectRefOwned,
        start: i32,
        end: i32,
    },
    Action {
        node: ObjectRefOwned,
        index: i32,
        identity: std::sync::Arc<Vec<(String, String, String, String)>>,
    },
    Cursor {
        root: Value,
        query: Value,
        view: Value,
        depth: u64,
        pending: Vec<Pending>,
        seen: HashSet<ObjectRefOwned>,
    },
}
#[derive(Clone)]
enum Pending {
    Node(ObjectRefOwned, u64),
    Children {
        node: ObjectRefOwned,
        depth: u64,
        next: i32,
        count: Option<i32>,
    },
}
struct Entry {
    value: Handle,
    touched: Instant,
    bytes: usize,
}
#[derive(Default)]
struct Registry {
    next: u64,
    entries: HashMap<String, Entry>,
    bytes: usize,
}
impl Registry {
    fn begin(&mut self) {
        self.entries
            .retain(|_, e| e.touched.elapsed() < Duration::from_secs(120));
        self.bytes = self.entries.values().map(|e| e.bytes).sum();
        if self.entries.len() > MAX_HANDLES / 2 || self.bytes > MAX_REGISTRY_BYTES / 2 {
            let mut order: Vec<_> = self
                .entries
                .iter()
                .map(|(id, e)| (id.clone(), e.touched))
                .collect();
            order.sort_by_key(|(_, t)| *t);
            for (id, _) in order {
                if self.entries.len() <= MAX_HANDLES / 2 && self.bytes <= MAX_REGISTRY_BYTES / 2 {
                    break;
                }
                if let Some(entry) = self.entries.remove(&id) {
                    self.bytes -= entry.bytes;
                }
            }
        }
    }
    fn put(&mut self, prefix: &str, value: Handle) -> Result<String, EIPError> {
        let bytes = value.retained_bytes();
        if self.entries.len() >= MAX_HANDLES
            || bytes > MAX_REGISTRY_BYTES.saturating_sub(self.bytes)
        {
            return Err(error(
                ErrorType::QuotaExceeded,
                "accessibility handle registry is full",
            ));
        }
        self.next += 1;
        let id = format!("{prefix}_{:x}", self.next);
        self.entries.insert(
            id.clone(),
            Entry {
                value,
                touched: Instant::now(),
                bytes,
            },
        );
        self.bytes += bytes;
        Ok(id)
    }
    fn get(&mut self, id: &str) -> Result<Handle, EIPError> {
        let entry = self.entries.get_mut(id).ok_or_else(stale)?;
        entry.touched = Instant::now();
        Ok(entry.value.clone())
    }
}

impl Handle {
    fn retained_bytes(&self) -> usize {
        let object = |n: &ObjectRefOwned| {
            256 + n.name_as_str().map_or(0, str::len) + n.path().as_str().len()
        };
        256 + match self {
            Self::Node(node) | Self::Range { node, .. } => object(node),
            Self::Action { node, identity, .. } => {
                object(node)
                    + identity
                        .iter()
                        .map(|i| {
                            128 + i.0.capacity() + i.1.capacity() + i.2.capacity() + i.3.capacity()
                        })
                        .sum::<usize>()
            }
            Self::Cursor {
                root,
                query,
                view,
                pending,
                seen,
                ..
            } => {
                json_bytes(root)
                    + json_bytes(query)
                    + json_bytes(view)
                    + pending
                        .iter()
                        .map(|p| match p {
                            Pending::Node(node, _) | Pending::Children { node, .. } => {
                                object(node) + 64
                            }
                        })
                        .sum::<usize>()
                    + seen.iter().map(|n| object(n) * 2).sum::<usize>()
            }
        }
    }
}
fn json_bytes(value: &Value) -> usize {
    64 + match value {
        Value::String(s) => s.capacity(),
        Value::Array(a) => a.iter().map(json_bytes).sum(),
        Value::Object(o) => o
            .iter()
            .map(|(k, v)| k.capacity() + 64 + json_bytes(v))
            .sum(),
        _ => 0,
    }
}

struct Budget {
    limits: Value,
    calls: Cell<u64>,
    deadline: Instant,
    reason: Cell<Option<&'static str>>,
    projection_bytes: usize,
}
impl Budget {
    fn new(limits: &Value) -> Self {
        Self {
            limits: limits.clone(),
            projection_bytes: 0,
            calls: Cell::new(0),
            deadline: Instant::now() + Duration::from_millis(number(limits, "timeout_ms", 5000)),
            reason: Cell::new(None),
        }
    }
    fn for_request(p: &Value) -> Self {
        let mut budget = Self::new(&p["limits"]);
        let remaining = number(p, "_timeout_ms", number(&p["limits"], "timeout_ms", 5000))
            .min(number(&p["limits"], "timeout_ms", 5000));
        // Leave time to serialize and deliver a partial read before the parent
        // deadline. Native mutations still await their reply under supervision.
        let reserve = (remaining / 10).min(50);
        budget.deadline = Instant::now() + Duration::from_millis(remaining - reserve);
        budget.projection_bytes = p["_projection_bytes"].as_u64().unwrap_or(0) as usize;
        budget
    }
    fn share(&self, peers: usize) -> Self {
        let peers = peers.max(1);
        let mut limits = self.limits.clone();
        let calls = self.calls.get();
        let remaining = (self.limit("max_native_calls") as u64).saturating_sub(calls);
        limits["max_native_calls"] = json!(calls + remaining.div_ceil(peers as u64));
        Self {
            limits,
            calls: Cell::new(calls),
            deadline: Instant::now()
                + self.deadline.saturating_duration_since(Instant::now()) / peers as u32,
            reason: Cell::new(None),
            projection_bytes: self.projection_bytes,
        }
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
        ) as usize
    }
    fn check(&self) -> Result<(), EIPError> {
        let reason = if Instant::now() >= self.deadline {
            Some("deadline")
        } else if self.calls.get() >= self.limit("max_native_calls") as u64 {
            Some("call_limit")
        } else {
            None
        };
        if let Some(reason) = reason {
            self.mark(reason);
            return Err(error(
                ErrorType::Timeout,
                "accessibility read budget exhausted",
            ));
        }
        Ok(())
    }
    async fn call<T>(&self, future: impl Future<Output = zbus::Result<T>>) -> Result<T, EIPError> {
        self.check()?;
        self.calls.set(self.calls.get() + 1);
        match tokio::time::timeout_at(self.deadline.into(), future).await {
            Ok(result) => result.map_err(native_error),
            Err(_) => {
                self.mark("deadline");
                Err(error(
                    ErrorType::Timeout,
                    "accessibility read deadline exhausted",
                ))
            }
        }
    }
    fn mark(&self, reason: &'static str) {
        if self.reason.get().is_none() || matches!(reason, "deadline" | "call_limit") {
            self.reason.set(Some(reason));
        }
    }
    fn completeness(&self) -> Value {
        match self.reason.get() {
            Some(reason) => json!({"complete":false,"reason":reason}),
            None => json!({"complete":true}),
        }
    }
    fn text(&self, text: String) -> String {
        self.string(text, self.limit("max_text_bytes"))
    }
    fn string(&self, mut text: String, max: usize) -> String {
        if text.len() > max {
            let mut end = max;
            while !text.is_char_boundary(end) {
                end -= 1;
            }
            text.truncate(end);
            self.mark("byte_limit");
        }
        text
    }
}

// Proxy construction has no remote calls and disables property caching: every
// property counted by Budget samples the provider, not a previous observation.
macro_rules! proxy {
    ($name:ident, $module:ident, $ty:ident) => {
        async fn $name(
            &self,
            node: &ObjectRefOwned,
        ) -> Result<atspi::$module::$ty<'static>, EIPError> {
            atspi::$module::$ty::builder(self.connection.as_ref().ok_or_else(unavailable)?)
                .destination(node.name_as_str().ok_or_else(stale)?.to_owned())
                .map_err(native_error)?
                .path(node.path().clone())
                .map_err(native_error)?
                .cache_properties(CacheProperties::No)
                .build()
                .await
                .map_err(native_error)
        }
    };
}
impl Native {
    proxy!(accessible, accessible, AccessibleProxy);
    proxy!(component, component, ComponentProxy);
    proxy!(action, action, ActionProxy);
    proxy!(text, text, TextProxy);
    proxy!(editable, editable_text, EditableTextProxy);
    proxy!(selection, selection, SelectionProxy);
    proxy!(value, value, ValueProxy);
    proxy!(document, document, DocumentProxy);
    proxy!(table, table, TableProxy);
    proxy!(cell, table_cell, TableCellProxy);
    proxy!(image, image, ImageProxy);
    proxy!(hypertext, hypertext, HypertextProxy);
    proxy!(hyperlink, hyperlink, HyperlinkProxy);

    async fn connect(&mut self) -> Result<(), EIPError> {
        if self.connection.is_some() {
            return Ok(());
        }
        if std::env::var_os("DISPLAY").is_none()
            || std::env::var_os("WAYLAND_DISPLAY").is_some()
            || std::env::var("XDG_SESSION_TYPE").is_ok_and(|s| s.eq_ignore_ascii_case("wayland"))
        {
            return Err(error(
                ErrorType::Unsupported,
                "AT-SPI requires the inherited X11 desktop",
            ));
        }
        // No override address, alternate user's bus, or fallback desktop.
        if std::env::var_os("DBUS_SESSION_BUS_ADDRESS").is_none() {
            return Err(unavailable());
        }
        let session = Connection::session().await.map_err(native_error)?;
        let bus = atspi::bus::BusProxy::new(&session)
            .await
            .map_err(native_error)?;
        let address = bus.get_address().await.map_err(native_error)?;
        let connection = zbus::connection::Builder::address(address.as_str())
            .map_err(native_error)?
            // The parent owns the absolute native deadline. A shorter D-Bus
            // timeout would turn valid slow replies into uncertain outcomes.
            .method_timeout(Duration::from_secs(30))
            .build()
            .await
            .map_err(native_error)?;
        self.connection = Some(connection);
        Ok(())
    }
    async fn root(&self, b: &Budget) -> Result<ObjectRefOwned, EIPError> {
        let dbus = zbus::fdo::DBusProxy::new(self.connection.as_ref().ok_or_else(unavailable)?)
            .await
            .map_err(native_error)?;
        let name = b
            .call(async {
                dbus.get_name_owner(REGISTRY.try_into()?)
                    .await
                    .map_err(Into::into)
            })
            .await?;
        Ok(ObjectRef::new_owned(
            name,
            zbus::zvariant::ObjectPath::try_from(ROOT).map_err(|_| stale())?,
        ))
    }
    fn reference(&mut self, node: ObjectRefOwned) -> Result<Value, EIPError> {
        if node.is_null() {
            return Err(stale());
        }
        let existing = self
            .registry
            .entries
            .iter()
            .find_map(|(id, e)| match &e.value {
                Handle::Node(value) if value == &node => Some(id.clone()),
                _ => None,
            });
        let id = match existing {
            Some(id) => {
                self.registry.get(&id)?;
                id
            }
            None => self.registry.put("an", Handle::Node(node))?,
        };
        Ok(json!({"backend":"atspi","backend_epoch":self.epoch,"node_handle":id}))
    }
    fn node(&mut self, reference: &Value) -> Result<ObjectRefOwned, EIPError> {
        if reference["backend"] != "atspi" || reference["backend_epoch"] != self.epoch {
            return Err(stale());
        }
        match self.registry.get(string(reference, "node_handle")?)? {
            Handle::Node(node) => Ok(node),
            _ => Err(stale()),
        }
    }
    async fn parent(&self, node: &ObjectRefOwned, b: &Budget) -> Result<ObjectRefOwned, EIPError> {
        let p = self.accessible(node).await?;
        // atspi-common 0.14's OwnedValue conversion for Parent parses the bus
        // name before recognizing the protocol's ("", null-path) sentinel.
        let (name, path): (String, zbus::zvariant::OwnedObjectPath) =
            b.call(p.inner().get_property("Parent")).await?;
        if path.as_str() == "/org/a11y/atspi/null" {
            return Ok(ObjectRef::Null.into());
        }
        Ok(ObjectRef::new_owned(
            zbus::names::UniqueName::try_from(name).map_err(|_| unavailable())?,
            path,
        ))
    }
    async fn live(&self, node: &ObjectRefOwned, b: &Budget) -> Result<(), EIPError> {
        let p = self.accessible(node).await?;
        let state = b.call(p.get_state()).await?;
        if state.contains(State::Defunct) {
            return Err(stale());
        }
        Ok(())
    }
    async fn protected(&self, node: &ObjectRefOwned, b: &Budget) -> Result<bool, EIPError> {
        Ok(b.call(self.accessible(node).await?.get_role()).await? == Role::PasswordText)
    }
    async fn require(
        &self,
        node: &ObjectRefOwned,
        interface: Interface,
        b: &Budget,
    ) -> Result<(), EIPError> {
        self.live(node, b).await?;
        let interfaces = b
            .call(self.accessible(node).await?.get_interfaces())
            .await?;
        if !interfaces.contains(interface) {
            return Err(error(
                ErrorType::Unsupported,
                "native interface is not supported by this object",
            ));
        }
        Ok(())
    }
    async fn execute(
        &mut self,
        method: &str,
        p: Value,
        dispatch: &mut dyn FnMut() -> Result<(), EIPError>,
    ) -> Result<Value, EIPError> {
        self.registry.begin();
        let b = Budget::for_request(&p);
        let connected = match tokio::time::timeout_at(b.deadline.into(), self.connect()).await {
            Ok(result) => result,
            Err(_) => {
                b.mark("deadline");
                Err(error(
                    ErrorType::Timeout,
                    "accessibility connection deadline exhausted",
                ))
            }
        };
        if method == "computer.a11y_describe" {
            let readiness = match connected {
                Ok(()) => self.root(&b).await.map(|_| ()),
                Err(err) => Err(err),
            };
            let reason = match &readiness {
                Ok(()) => "ready",
                Err(err) => match err.data.error_type {
                    ErrorType::Unsupported => "unsupported",
                    ErrorType::Denied => "permission_required",
                    _ => "bus_unavailable",
                },
            };
            let ready = readiness.is_ok();
            return Ok(
                json!({"backend":"atspi","backend_epoch":self.epoch,"ready":ready,
                "reason":reason,
                "desktop_identity":format!("x11:{}",std::env::var("DISPLAY").unwrap_or_default()),
                "methods":METHODS,"frames":["atspi_screen","atspi_window","atspi_parent"],
                "limits":{"depth":2,"max_nodes":256,"max_matches":64,"max_items":128,"max_text_bytes":16384,"max_result_bytes":262144,"max_native_calls":1024,"timeout_ms":5000}}),
            );
        }
        connected?;
        let mut result = match method {
            "computer.a11y_roots" => self.roots(&p["scope"], &b).await?,
            "computer.a11y_inspect" | "computer.a11y_find" => {
                self.tree(&p, &b, method.ends_with("find")).await?
            }
            "computer.a11y_read" => self.read(&p, &b).await?,
            "computer.a11y_range" => self.range(&p, &b).await?,
            "computer.a11y_range_read" => self.range_read(&p, &b).await?,
            _ if METHODS.contains(&method) => return self.mutate(method, &p, &b, dispatch).await,
            _ => return Err(error(ErrorType::Unsupported, "unsupported AT-SPI method")),
        };
        bound_collections(
            &mut result,
            &b,
            matches!(
                method,
                "computer.a11y_roots" | "computer.a11y_inspect" | "computer.a11y_find"
            ),
        );
        if let Some(reason) = b.reason.get() {
            result["completeness"]["complete"] = json!(false);
            result["completeness"]["reason"] = json!(reason);
        }
        if matches!(method, "computer.a11y_inspect" | "computer.a11y_find") {
            // Traversal already committed its cursor: never discard emitted nodes.
            if super::budget::result_bytes(method, &result, b.projection_bytes)?
                > b.limit("max_result_bytes")
            {
                return Err(error(
                    ErrorType::InternalError,
                    "traversal result exceeded its reserved budget",
                ));
            }
        } else {
            fit_result(
                &mut result,
                method,
                b.limit("max_result_bytes"),
                b.projection_bytes,
            )?;
        }
        Ok(result)
    }
    async fn children(
        &mut self,
        node: &ObjectRefOwned,
        b: &Budget,
    ) -> Result<Vec<ObjectRefOwned>, EIPError> {
        let p = self.accessible(node).await?;
        let count = b.call(p.child_count()).await?.max(0) as usize;
        if count > b.limit("max_nodes") {
            b.mark("node_limit");
        }
        let mut out = Vec::new();
        for i in 0..count.min(b.limit("max_nodes")) {
            if b.check().is_err() {
                break;
            }
            match b.call(p.get_child_at_index(i as i32)).await {
                Ok(child) if !child.is_null() => out.push(child),
                Err(_) if b.check().is_err() => break,
                // Registry and toolkit child arrays are live: a disappeared
                // child does not invalidate the remaining native siblings.
                Err(err) if err.data.error_type == ErrorType::Conflict => continue,
                Err(err) => return Err(err),
                _ => continue,
            }
        }
        Ok(out)
    }
    async fn roots(&mut self, scope: &Value, b: &Budget) -> Result<Value, EIPError> {
        let root = self.root(b).await?;
        let kind = scope["kind"].as_str().unwrap_or("windows");
        let mut nodes = Vec::new();
        let read = async {
            if kind == "desktop" {
                nodes.push(self.sample(&root, b).await?);
            } else {
                let applications = self.children(&root, b).await?;
                let count = applications.len();
                for (index, app) in applications.into_iter().enumerate() {
                    if b.check().is_err() {
                        break;
                    }
                    // Reserve a fair share of the remaining read budget for
                    // every registry entry. A hung app cannot consume the time
                    // and calls reserved for all later applications.
                    let app_budget = b.share(count - index);
                    let read = async {
                        let b = &app_budget;
                        if kind == "application" {
                            // PID comes from the bus daemon, not an app-provided label.
                            let dbus = zbus::fdo::DBusProxy::new(
                                self.connection.as_ref().ok_or_else(unavailable)?,
                            )
                            .await
                            .map_err(native_error)?;
                            let pid = b
                                .call(async {
                                    dbus.get_connection_unix_process_id(
                                        app.name()
                                            .ok_or(zbus::Error::Failure("missing owner".into()))?
                                            .clone()
                                            .into(),
                                    )
                                    .await
                                    .map_err(Into::into)
                                })
                                .await?;
                            if Some(i64::from(pid)) != scope["process_id"].as_i64() {
                                return Ok(());
                            }
                        }
                        if kind == "applications" || kind == "application" {
                            nodes.push(self.sample(&app, b).await?);
                        } else if kind == "windows" {
                            for window in self.children(&app, b).await? {
                                if nodes.len() >= b.limit("max_nodes") {
                                    b.mark("node_limit");
                                    break;
                                }
                                match self.sample(&window, b).await {
                                    Ok(sample) => nodes.push(sample),
                                    Err(err) if err.data.error_type == ErrorType::Conflict => {
                                        continue;
                                    }
                                    Err(err) => return Err(err),
                                }
                            }
                        } else if kind == "focused" {
                            // Active is a top-level window state; visibility on an
                            // arbitrary descendant does not prove focus is absent.
                            let mut pending = Vec::new();
                            for window in self.children(&app, b).await? {
                                match b.call(self.accessible(&window).await?.get_state()).await {
                                    Ok(state)
                                        if state.contains(State::Active)
                                            && !state.contains(State::Defunct) =>
                                    {
                                        pending.push(window)
                                    }
                                    Ok(_) => {}
                                    Err(err) if err.data.error_type == ErrorType::Conflict => {
                                        continue;
                                    }
                                    Err(err) => return Err(err),
                                }
                            }
                            pending.reverse();
                            let mut seen = HashSet::new();
                            while let Some(node) = pending.pop() {
                                if !seen.insert(node.clone()) {
                                    continue;
                                }
                                if seen.len() > b.limit("max_nodes")
                                    || nodes.len() >= b.limit("max_nodes")
                                {
                                    b.mark("node_limit");
                                    break;
                                }
                                let state =
                                    match b.call(self.accessible(&node).await?.get_state()).await {
                                        Ok(state) => state,
                                        Err(err) if err.data.error_type == ErrorType::Conflict => {
                                            continue;
                                        }
                                        Err(err) => return Err(err),
                                    };
                                if state.contains(State::Defunct) {
                                    continue;
                                }
                                if state.contains(State::Focused) {
                                    nodes.push(self.sample(&node, b).await?);
                                    break;
                                }
                                pending.extend(self.children(&node, b).await?.into_iter().rev());
                            }
                        } else if kind == "at_point" {
                            let (frame, x, y) = point(&scope["point"])?;
                            // Application roots need not expose Component. Hit testing
                            // starts at their top-level windows, then follows native hits.
                            for window in self.children(&app, b).await? {
                                let state =
                                    b.call(self.accessible(&window).await?.get_state()).await?;
                                if state.contains(State::Defunct)
                                    || state.contains(State::Iconified)
                                    || !state.contains(State::Showing)
                                    || !state.contains(State::Visible)
                                {
                                    continue;
                                }
                                if !b
                                    .call(self.component(&window).await?.contains(x, y, frame))
                                    .await?
                                {
                                    continue;
                                }
                                let mut current = window;
                                let mut seen = HashSet::new();
                                loop {
                                    if !seen.insert(current.clone()) {
                                        return Err(error(
                                            ErrorType::ProviderUnavailable,
                                            "native hit-test cycle",
                                        ));
                                    }
                                    let p = self.component(&current).await?;
                                    match b.call(p.get_accessible_at_point(x, y, frame)).await {
                                        Ok(next) if !next.is_null() && next != current => {
                                            current = next
                                        }
                                        Ok(_) => break,
                                        Err(_) if b.check().is_err() => break,
                                        Err(err) => return Err(err),
                                    }
                                }
                                nodes.push(self.sample(&current, b).await?);
                                if nodes.len() >= b.limit("max_nodes") {
                                    b.mark("node_limit");
                                    break;
                                }
                            }
                        } else {
                            return Err(error(ErrorType::InvalidParams, "invalid root scope"));
                        }
                        Ok::<(), EIPError>(())
                    }
                    .await;
                    b.calls.set(app_budget.calls.get());
                    if let Some(reason) = app_budget.reason.get() {
                        b.mark(reason);
                    }
                    if let Err(err) = read
                        && err.data.error_type != ErrorType::Conflict
                        && !matches!(app_budget.reason.get(), Some("call_limit" | "deadline"))
                    {
                        return Err(err);
                    }
                    if kind == "focused" && !nodes.is_empty() {
                        break;
                    }
                    if nodes.len() >= b.limit("max_nodes") {
                        b.mark("node_limit");
                        break;
                    }
                }
            }
            Ok::<(), EIPError>(())
        }
        .await;
        if let Err(err) = read
            && b.check().is_ok()
        {
            return Err(err);
        }
        Ok(
            json!({"backend":"atspi","sampled_at":now(),"nodes":nodes,"completeness":b.completeness()}),
        )
    }
    async fn sample(&mut self, node: &ObjectRefOwned, b: &Budget) -> Result<Value, EIPError> {
        Ok(self.sample_query(node, b, &Value::Null).await?.0)
    }
    async fn sample_query(
        &mut self,
        node: &ObjectRefOwned,
        b: &Budget,
        query: &Value,
    ) -> Result<(Value, Result<bool, EIPError>), EIPError> {
        let p = self.accessible(node).await?;
        let native_role = b.call(p.get_role_name()).await?;
        let role = b.string(native_role.clone(), 1024);
        let state = b.call(p.get_state()).await?;
        if state.contains(State::Defunct) {
            return Err(stale());
        }
        let protected = self.protected(node, b).await?;
        let reference = self.reference(node.clone())?;
        let mut result = json!({"reference":reference,"native_role":role,"role":role.replace(' ',"_"),
            "states":{},"children":[],"capabilities":[],"sampled_at":now(),"field_errors":[]});
        // GetState returns the full native bitset: absent defined bits are
        // observed false, whereas an unknown state name remains absent.
        for bit in 0..64 {
            if let Ok(defined) = atspi::common::StateSet::from_bits(1u64 << bit) {
                for s in defined.iter() {
                    result["states"][s.to_string()] = json!(state.contains(s));
                }
            }
        }
        result["states"]["protected"] = json!(protected);
        // Predicate evidence is request-local native data, independent of the
        // bounded display projection. Never retain it in a traversal cursor.
        let mut predicate = json!({"native_role":native_role,"role":native_role.replace(' ', "_"),
            "states":result["states"],"capabilities":[]});
        let mut query_errors = Vec::new();
        for (field, value) in [
            ("name", b.call(p.name()).await),
            ("description", b.call(p.description()).await),
            ("identifier", b.call(p.accessible_id()).await),
        ] {
            match value {
                Ok(text) => {
                    if !query[field].is_null() {
                        predicate[field] = json!(text);
                    }
                    result[field] = json!(b.text(text));
                }
                Err(err) => {
                    field_error(&mut result, field, &err);
                    if !query[field].is_null() {
                        query_errors.push((field, err));
                    }
                }
            }
        }
        match self.parent(node, b).await {
            Ok(parent) if !parent.is_null() => result["parent"] = self.reference(parent)?,
            Err(err) => field_error(&mut result, "parent", &err),
            _ => {}
        }
        match b.call(p.child_count()).await {
            Ok(count) => result["child_count"] = json!(count.max(0)),
            Err(err) => field_error(&mut result, "child_count", &err),
        }
        let interfaces = match b.call(p.get_interfaces()).await {
            Ok(interfaces) => interfaces,
            Err(err) => {
                field_error(&mut result, "interfaces", &err);
                if !query["operation"].is_null() {
                    query_errors.push(("operation", err));
                }
                return Ok((result, resolve_query(&predicate, query, query_errors)));
            }
        };
        if interfaces.contains(Interface::Component) {
            match b
                .call(self.component(node).await?.get_extents(CoordType::Screen))
                .await
            {
                Ok(rect) if rect.2 >= 0 && rect.3 >= 0 => {
                    result["bounds"] = bounds(rect, "atspi_screen")
                }
                Err(err) => field_error(&mut result, "bounds", &err),
                _ => {}
            }
        }
        let mut capabilities = Vec::new();
        for (interface, ops) in mutation::CAPABILITIES {
            if interfaces.contains(*interface) && !protected {
                for op in *ops {
                    capabilities.push(json!({"operation":op,"supported":"candidate","source":format!("atspi.{interface:?}")}));
                }
            }
        }
        if interfaces.contains(Interface::Action) && !protected {
            match self.actions(node, b).await {
                Ok(actions) => {
                    if actions.iter().any(|identity| {
                        !identity.0.is_empty() && identity.0.chars().count() <= 1024
                    }) {
                        predicate["capabilities"]
                            .as_array_mut()
                            .unwrap()
                            .push(json!({"operation":"do_action"}));
                    }
                    for (index, identity) in actions.iter().enumerate() {
                        // Action identities cannot be truncated and then used
                        // as native identifiers. Keep them exact or omit them.
                        if identity.0.is_empty() || identity.0.chars().count() > 1024 {
                            field_error(&mut result, "actions", &unavailable());
                            continue;
                        }
                        if capabilities.len() >= b.limit("max_items").min(128) {
                            b.mark("item_limit");
                            break;
                        }
                        let id = self.registry.put(
                            "aa",
                            Handle::Action {
                                node: node.clone(),
                                index: index as i32,
                                identity: actions.clone(),
                            },
                        )?;
                        capabilities.push(json!({"operation":"do_action","supported":"yes","source":"atspi.Action",
                    "action":{"node":reference,"action_handle":id,"native_identifier":identity.0},"label":b.text(identity.1.clone()),"description":b.text(identity.2.clone())}));
                    }
                }
                Err(err) => {
                    field_error(&mut result, "actions", &err);
                    if query["operation"] == "do_action" {
                        query_errors.push(("operation", err));
                    }
                }
            }
        }
        predicate["capabilities"]
            .as_array_mut()
            .unwrap()
            .extend(capabilities.iter().cloned());
        if capabilities.len() > b.limit("max_items").min(128) {
            capabilities.truncate(b.limit("max_items").min(128));
            b.mark("item_limit");
        }
        result["capabilities"] = json!(capabilities);
        Ok((result, resolve_query(&predicate, query, query_errors)))
    }
    async fn actions(
        &self,
        node: &ObjectRefOwned,
        b: &Budget,
    ) -> Result<std::sync::Arc<Vec<(String, String, String, String)>>, EIPError> {
        let p = self.action(node).await?;
        let count = b.call(p.n_actions()).await?.max(0) as usize;
        if count > b.limit("max_items").min(128) {
            b.mark("item_limit");
        }
        let mut out = Vec::new();
        let mut bytes = 0;
        // Retain a stable bounded prefix independent of read pagination; an
        // action reference must revalidate with the same native index set.
        for i in 0..count.min(128) {
            if b.check().is_err() {
                break;
            }
            let identity = (
                b.call(p.get_name(i as i32)).await?,
                b.call(p.get_localized_name(i as i32)).await?,
                b.call(p.get_description(i as i32)).await?,
                b.call(p.get_key_binding(i as i32)).await?,
            );
            bytes += identity.0.capacity()
                + identity.1.capacity()
                + identity.2.capacity()
                + identity.3.capacity();
            if bytes > MAX_ACTION_BYTES {
                b.mark("item_limit");
                break;
            }
            out.push(identity);
        }
        Ok(std::sync::Arc::new(out))
    }
    async fn tree(&mut self, p: &Value, b: &Budget, find: bool) -> Result<Value, EIPError> {
        let root = self.node(&p["node"])?;
        let query = if find {
            p["query"].clone()
        } else {
            Value::Null
        };
        let mut pending = vec![Pending::Node(root.clone(), 0)];
        let mut seen = HashSet::new();
        if !p["cursor"].is_null() {
            if p["cursor"]["root"] != p["node"] {
                return Err(stale());
            }
            match self.registry.get(string(&p["cursor"], "cursor_handle")?)? {
                Handle::Cursor {
                    root,
                    query: old,
                    view,
                    depth,
                    pending: q,
                    seen: s,
                } if root == p["node"]
                    && old == query
                    && view == p["view"]
                    && depth == b.limit("depth") as u64 =>
                {
                    pending = q;
                    seen = s;
                }
                _ => return Err(stale()),
            }
        }
        // Child enumeration is lazy. A wide native container never expands a
        // breadth-first queue or loses its unenumerated suffix at a page limit.
        let sampled_at = now();
        let mut nodes = Vec::new();
        let mut visited = 0;
        let mut progressed = false;
        while !pending.is_empty() {
            if visited >= b.limit("max_nodes")
                || nodes.len()
                    >= if find {
                        b.limit("max_matches")
                    } else {
                        b.limit("max_nodes")
                    }
            {
                b.mark("node_limit");
                break;
            }
            if b.check().is_err() {
                break;
            }
            // Validate live scope before consuming either kind of retained
            // work. An interrupted ancestry read leaves the cursor unchanged.
            let candidate = match pending.last().unwrap() {
                Pending::Node(node, _) | Pending::Children { node, .. } => node,
            };
            match self.descendant(candidate, &root, b).await {
                Ok(true) => {}
                Err(_) if b.check().is_err() => break,
                Ok(false) => {
                    tree_remove_child(&mut nodes, &self.reference(candidate.clone())?, &stale());
                    pending.pop();
                    progressed = true;
                    continue;
                }
                Err(err) if err.data.error_type == ErrorType::Conflict => {
                    let reference = self.reference(candidate.clone())?;
                    tree_remove_child(&mut nodes, &reference, &err);
                    tree_field_error(&mut nodes, &reference, "children", &err);
                    pending.pop();
                    progressed = true;
                    continue;
                }
                Err(err) => return Err(err),
            }
            match pending.pop().unwrap() {
                Pending::Children {
                    node,
                    depth,
                    next,
                    count,
                } => {
                    let proxy = self.accessible(&node).await?;
                    let count = match count {
                        Some(count) => count,
                        None => match b.call(proxy.child_count()).await {
                            Ok(count) => count.max(0),
                            Err(_) if b.check().is_err() => {
                                pending.push(Pending::Children {
                                    node,
                                    depth,
                                    next,
                                    count: None,
                                });
                                break;
                            }
                            Err(err) if err.data.error_type == ErrorType::Conflict => {
                                tree_field_error(
                                    &mut nodes,
                                    &self.reference(node)?,
                                    "children",
                                    &err,
                                );
                                progressed = true;
                                continue;
                            }
                            Err(err) => return Err(err),
                        },
                    };
                    if next >= count {
                        progressed = true;
                        continue;
                    }
                    let child = b.call(proxy.get_child_at_index(next)).await;
                    let advance = child.is_ok() || b.check().is_ok();
                    pending.push(Pending::Children {
                        node: node.clone(),
                        depth,
                        next: next + i32::from(advance),
                        count: Some(count),
                    });
                    match child {
                        Ok(child) if !child.is_null() && !seen.contains(&child) => {
                            let reference = self.reference(child.clone())?;
                            let parent_ref = self.reference(node.clone())?;
                            if let Some(index) = nodes
                                .iter()
                                .position(|v: &Value| v["reference"] == parent_ref)
                            {
                                let children = nodes[index]["children"].as_array_mut().unwrap();
                                if children.len() < b.limit("max_items").min(512) {
                                    children.push(reference);
                                    if !tree_fits(
                                        &nodes,
                                        &p["node"],
                                        &sampled_at,
                                        b.limit("max_result_bytes"),
                                        b.projection_bytes,
                                    ) {
                                        nodes[index]["children"].as_array_mut().unwrap().pop();
                                        b.mark("byte_limit");
                                    }
                                } else {
                                    b.mark("item_limit");
                                }
                            }
                            pending.push(Pending::Node(child, depth));
                        }
                        Err(_) if !advance => break,
                        Err(err) if err.data.error_type == ErrorType::Conflict => {
                            tree_field_error(&mut nodes, &self.reference(node)?, "children", &err);
                            progressed = true;
                        }
                        Err(err) => return Err(err),
                        Ok(child) if child.is_null() => {
                            tree_field_error(
                                &mut nodes,
                                &self.reference(node)?,
                                "children",
                                &stale(),
                            );
                            progressed = true;
                        }
                        _ => {}
                    }
                }
                Pending::Node(node, depth) => {
                    if seen.contains(&node) {
                        continue;
                    }
                    // A traversal is finite even if a provider continuously
                    // adds children. Separate pages do not reset this bound.
                    if seen.len() >= MAX_HANDLES {
                        b.mark("node_limit");
                        pending.clear();
                        break;
                    }
                    visited += 1;
                    let (sample, matched) = match self.sample_query(&node, b, &query).await {
                        Ok(sample) => sample,
                        Err(_) if b.check().is_err() => {
                            pending.push(Pending::Node(node, depth));
                            break;
                        }
                        Err(err) if depth > 0 && err.data.error_type == ErrorType::Conflict => {
                            // A vanished object is absent from this live traversal.
                            // It must not discard observations of surviving siblings.
                            let reference = self.reference(node.clone())?;
                            tree_remove_child(&mut nodes, &reference, &err);
                            seen.insert(node);
                            progressed = true;
                            continue;
                        }
                        Err(err) => return Err(err),
                    };
                    // Optional fields can report budget errors in an otherwise
                    // valid sample. Do not commit an unresolved find predicate
                    // or a degraded inspect node to the cursor's seen set.
                    if matches!(b.reason.get(), Some("call_limit" | "deadline"))
                        || Instant::now() >= b.deadline
                    {
                        b.check().ok();
                        pending.push(Pending::Node(node, depth));
                        break;
                    }
                    let count = sample["child_count"].as_i64().map(|n| n as i32);
                    if !find || matched? {
                        nodes.push(sample);
                        if !tree_fits(
                            &nodes,
                            &p["node"],
                            &sampled_at,
                            b.limit("max_result_bytes"),
                            b.projection_bytes,
                        ) {
                            nodes.pop();
                            if nodes.is_empty() {
                                return Err(error(
                                    ErrorType::QuotaExceeded,
                                    "result byte budget cannot hold the next accessibility node",
                                ));
                            }
                            pending.push(Pending::Node(node, depth));
                            b.mark("byte_limit");
                            break;
                        }
                    }
                    seen.insert(node.clone());
                    progressed = true;
                    if depth < b.limit("depth") as u64 && count != Some(0) {
                        pending.push(Pending::Children {
                            node,
                            depth: depth + 1,
                            next: 0,
                            count,
                        });
                    } else if count.is_none_or(|n| n > 0) {
                        b.mark("node_limit");
                    }
                }
            }
        }
        if !pending.is_empty() && !progressed {
            return Err(error(
                ErrorType::Timeout,
                "accessibility traversal made no progress; retry the read with a larger budget or reacquire its root",
            ));
        }
        let mut complete = b.completeness();
        if !pending.is_empty() {
            complete["complete"] = json!(false);
            let id = self.registry.put(
                "ac",
                Handle::Cursor {
                    root: p["node"].clone(),
                    query,
                    view: p["view"].clone(),
                    depth: b.limit("depth") as u64,
                    pending,
                    seen,
                },
            )?;
            complete["continuation"] = json!({"root":p["node"],"cursor_handle":id});
        }
        Ok(json!({"backend":"atspi","sampled_at":sampled_at,"nodes":nodes,"completeness":complete}))
    }
}

fn bound_collections(value: &mut Value, b: &Budget, tree: bool) {
    if let Some(object) = value.as_object_mut() {
        for (key, child) in object {
            if matches!(key.as_str(), "field_errors" | "capabilities" | "states") {
                continue;
            }
            if let Some(items) = child.as_array_mut() {
                let max = if tree && key == "nodes" {
                    b.limit("max_nodes")
                } else {
                    b.limit("max_items")
                };
                if items.len() > max {
                    items.truncate(max);
                    b.mark("item_limit");
                }
                for item in items {
                    bound_collections(item, b, false);
                }
            } else {
                bound_collections(child, b, false);
            }
        }
    }
}
fn tree_fits(
    nodes: &[Value],
    root: &Value,
    sampled_at: &str,
    max: usize,
    projection_bytes: usize,
) -> bool {
    // Reserve the actual envelope and the longest possible continuation ID
    // before advancing native traversal past an emitted observation.
    super::budget::result_bytes(
        "computer.a11y_inspect",
        &json!({"backend":"atspi","sampled_at":sampled_at,"nodes":nodes,
        "completeness":{"complete":false,"reason":"byte_limit",
            "continuation":{"root":root,"cursor_handle":"ac_ffffffffffffffff"}}}),
        projection_bytes,
    )
    .is_ok_and(|bytes| bytes <= max)
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
fn number(v: &Value, key: &str, default: u64) -> u64 {
    v[key].as_u64().unwrap_or(default)
}
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str, EIPError> {
    v[key]
        .as_str()
        .ok_or_else(|| error(ErrorType::InvalidParams, "missing accessibility operand"))
}
fn integer(v: &Value, key: &str) -> Result<i32, EIPError> {
    v[key]
        .as_i64()
        .and_then(|n| i32::try_from(n).ok())
        .ok_or_else(|| {
            error(
                ErrorType::InvalidParams,
                "operand exceeds native integer domain",
            )
        })
}
fn stale() -> EIPError {
    error(
        ErrorType::Conflict,
        "accessibility reference is stale or belongs to another owner",
    )
}
fn unavailable() -> EIPError {
    error(
        ErrorType::ProviderUnavailable,
        "inherited desktop accessibility bus is unavailable",
    )
}
fn native_error(e: zbus::Error) -> EIPError {
    let kind = match &e {
        zbus::Error::MethodError(name, _, _)
            if name.as_str().contains("UnknownMethod")
                || name.as_str().contains("UnknownInterface") =>
        {
            ErrorType::Unsupported
        }
        zbus::Error::MethodError(name, _, _)
            if name.as_str().contains("UnknownObject")
                || name.as_str().contains("NameHasNoOwner")
                || name.as_str().contains("ServiceUnknown") =>
        {
            ErrorType::Conflict
        }
        zbus::Error::MethodError(name, _, _) if name.as_str().contains("AccessDenied") => {
            ErrorType::Denied
        }
        zbus::Error::FDO(err) => match err.as_ref() {
            zbus::fdo::Error::UnknownObject(_)
            | zbus::fdo::Error::NameHasNoOwner(_)
            | zbus::fdo::Error::ServiceUnknown(_) => ErrorType::Conflict,
            zbus::fdo::Error::UnknownMethod(_) | zbus::fdo::Error::UnknownInterface(_) => {
                ErrorType::Unsupported
            }
            zbus::fdo::Error::AccessDenied(_) => ErrorType::Denied,
            _ => ErrorType::ProviderUnavailable,
        },
        _ => ErrorType::ProviderUnavailable,
    };
    error(kind, "native accessibility request failed")
}
fn field_error(result: &mut Value, field: &str, e: &EIPError) {
    let reason = match e.data.error_type {
        ErrorType::Unsupported => "unsupported",
        ErrorType::Denied => "denied",
        ErrorType::Conflict => "stale",
        ErrorType::Timeout => "timeout",
        _ => "unavailable",
    };
    if let Some(errors) = result["field_errors"].as_array_mut()
        && errors.len() < 128
    {
        errors.push(json!({"field":field,"reason":reason}));
    }
}
fn tree_remove_child(nodes: &mut [Value], reference: &Value, err: &EIPError) {
    for parent in nodes {
        if let Some(children) = parent["children"].as_array_mut()
            && children.contains(reference)
        {
            children.retain(|child| child != reference);
            field_error(parent, "children", err);
        }
    }
}
fn tree_field_error(nodes: &mut [Value], reference: &Value, field: &str, err: &EIPError) {
    if let Some(parent) = nodes
        .iter_mut()
        .find(|node| node["reference"] == *reference)
    {
        field_error(parent, field, err);
    }
}
fn bounds((x, y, w, h): (i32, i32, i32, i32), frame: &str) -> Value {
    json!({"frame":frame,"x":x,"y":y,"width":w.max(0),"height":h.max(0)})
}
fn point(p: &Value) -> Result<(CoordType, i32, i32), EIPError> {
    let frame = match p["frame"].as_str() {
        Some("atspi_screen") => CoordType::Screen,
        Some("atspi_window") => CoordType::Window,
        Some("atspi_parent") => CoordType::Parent,
        _ => {
            return Err(error(
                ErrorType::InvalidParams,
                "AT-SPI coordinate frame required",
            ));
        }
    };
    let n = |key: &str| -> Result<i32, EIPError> {
        let n = p[key]
            .as_f64()
            .ok_or_else(|| error(ErrorType::InvalidParams, "native coordinate required"))?;
        if !n.is_finite() || n.fract() != 0. || n < i32::MIN as f64 || n > i32::MAX as f64 {
            return Err(error(
                ErrorType::InvalidParams,
                "native coordinates must be signed 32-bit pixels",
            ));
        }
        Ok(n as i32)
    };
    Ok((frame, n("x")?, n("y")?))
}
fn resolve_query(
    node: &Value,
    query: &Value,
    errors: Vec<(&str, EIPError)>,
) -> Result<bool, EIPError> {
    // A known false conjunct resolves the query even if another native field
    // failed. Otherwise preserve the original typed failure, not a nonmatch.
    let mut resolved = query.clone();
    for (field, _) in &errors {
        resolved[*field] = Value::Null;
    }
    if !matches_query(node, &resolved) {
        return Ok(false);
    }
    match errors.into_iter().next() {
        Some((_, err)) => Err(err),
        None => Ok(true),
    }
}
fn matches_query(node: &Value, q: &Value) -> bool {
    for key in ["role", "native_role", "identifier"] {
        if !q[key].is_null() && q[key] != node[key] {
            return false;
        }
    }
    if let Some(name) = q["name"].as_str() {
        let actual = node["name"].as_str().unwrap_or("");
        if if q["name_match"] == "substring_casefold" {
            !actual
                .case_fold()
                .collect::<String>()
                .contains(&name.case_fold().collect::<String>())
        } else {
            actual != name
        } {
            return false;
        }
    }
    if let Some(states) = q["states"].as_object() {
        for (key, value) in states {
            if node["states"][key] != *value {
                return false;
            }
        }
    }
    if let Some(op) = q["operation"].as_str()
        && !node["capabilities"]
            .as_array()
            .is_some_and(|caps| caps.iter().any(|cap| cap["operation"] == op))
    {
        return false;
    }
    true
}
// Enforce the serialized envelope, not just content text. A budget smaller than
// the minimal typed envelope cannot return a usable result.
fn fit_result(
    result: &mut Value,
    method: &str,
    max: usize,
    projection_bytes: usize,
) -> Result<(), EIPError> {
    while super::budget::result_bytes(method, result, projection_bytes)? > max {
        result["completeness"] = json!({"complete":false,"reason":"byte_limit"});
        let mut removed = false;
        for key in [
            "nodes",
            "properties",
            "ranges",
            "bounds",
            "attribute_runs",
            "document_selections",
            "attributes",
            "field_errors",
        ] {
            if let Some(items) = result.get_mut(key).and_then(Value::as_array_mut)
                && items.pop().is_some()
            {
                removed = true;
                break;
            }
        }
        if !removed {
            if let Some(text) = result["text"]["text"].as_str()
                && !text.is_empty()
            {
                let mut end = text.len() / 2;
                while !text.is_char_boundary(end) {
                    end -= 1;
                }
                let retained = &text[..end];
                let length = retained.chars().count();
                let status = if retained.is_empty() { "empty" } else { "text" };
                result["text"]["text"] = json!(retained);
                result["text"]["status"] = json!(status);
                if let Some(start) = result["text"]["span"]["start"].as_i64() {
                    result["text"]["span"]["end"] = json!(start + length as i64);
                }
                continue;
            }
            return Err(error(
                ErrorType::QuotaExceeded,
                "result byte budget cannot hold the accessibility envelope",
            ));
        }
    }
    Ok(())
}
