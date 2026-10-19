//! Session-owned CLIPBOARD selection service on the existing X connection.
use super::{clipboard::*, x11::*, *};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
    mpsc,
};
use x11rb::{
    connection::Connection,
    protocol::{Event, xproto::*},
    rust_connection::RustConnection,
    wrapper::ConnectionExt as _,
};

pub(super) struct Clipboard {
    commands: mpsc::Sender<Command>,
    worker: Option<std::thread::JoinHandle<()>>,
}
enum Command {
    Read(
        u32,
        Arc<AtomicBool>,
        mpsc::Sender<Result<ComputerReadClipboardResult, EIPError>>,
    ),
    Write(
        String,
        Arc<AtomicBool>,
        mpsc::Sender<Result<Effect, EIPError>>,
    ),
}

impl Clipboard {
    pub(super) fn start(connection: Arc<RustConnection>, root: u32) -> Result<Self, EIPError> {
        let state = State::new(connection, root)?;
        let (commands, receiver) = mpsc::channel();
        let worker = std::thread::Builder::new()
            .name("envd-clipboard".into())
            .spawn(move || state.run(receiver))
            .map_err(|_| unavailable())?;
        Ok(Self {
            commands,
            worker: Some(worker),
        })
    }
    pub(super) fn read(
        &self,
        max: u32,
        interrupted: &dyn Fn() -> bool,
    ) -> Result<ComputerReadClipboardResult, EIPError> {
        let (tx, rx) = mpsc::channel();
        let cancel = Arc::new(AtomicBool::new(false));
        self.commands
            .send(Command::Read(max, cancel.clone(), tx))
            .map_err(|_| unavailable())?;
        wait(rx, cancel, interrupted)
    }
    pub(super) fn write(
        &self,
        text: &str,
        interrupted: &dyn Fn() -> bool,
    ) -> Result<Effect, EIPError> {
        let (tx, rx) = mpsc::channel();
        let cancel = Arc::new(AtomicBool::new(false));
        self.commands
            .send(Command::Write(text.into(), cancel.clone(), tx))
            .map_err(|_| unavailable())?;
        wait(rx, cancel, interrupted)
    }
}
impl Drop for Clipboard {
    fn drop(&mut self) {
        // Disconnect commands, then join. Destroying only our window cannot erase
        // a selection that another client acquired meanwhile.
        let (replacement, _) = mpsc::channel();
        drop(std::mem::replace(&mut self.commands, replacement));
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
fn wait<T>(
    rx: mpsc::Receiver<Result<T, EIPError>>,
    cancel: Arc<AtomicBool>,
    interrupted: &dyn Fn() -> bool,
) -> Result<T, EIPError> {
    loop {
        if interrupted() {
            cancel.store(true, Ordering::Release);
        }
        match rx.recv_timeout(Duration::from_millis(5)) {
            Ok(result) => return result,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(_) => return Err(unavailable()),
        }
    }
}

// X timestamps wrap every 2^32 milliseconds; compare within the half-range.
fn request_is_current(time: u32, acquired: u32) -> bool {
    time == x11rb::CURRENT_TIME || (time.wrapping_sub(acquired) as i32) >= 0
}

struct State {
    connection: Arc<RustConnection>,
    root: u32,
    window: u32,
    selection: u32,
    targets: u32,
    utf8: u32,
    plain: u32,
    incr: u32,
    timestamp: u32,
    multiple: u32,
    property: u32,
    acquired: u32,
    text: Option<String>,
}
impl State {
    fn new(connection: Arc<RustConnection>, root: u32) -> Result<Self, EIPError> {
        let atom = |name: &[u8]| -> Result<u32, EIPError> {
            Ok(connection
                .intern_atom(false, name)
                .map_err(|_| unavailable())?
                .reply()
                .map_err(|_| unavailable())?
                .atom)
        };
        let selection = atom(b"CLIPBOARD")?;
        let targets = atom(b"TARGETS")?;
        let utf8 = atom(b"UTF8_STRING")?;
        let plain = atom(b"text/plain;charset=utf-8")?;
        let incr = atom(b"INCR")?;
        let timestamp = atom(b"TIMESTAMP")?;
        let multiple = atom(b"MULTIPLE")?;
        let property = atom(b"_ENVD_CLIPBOARD")?;
        let window = Self::window(&connection, root)?;
        Ok(Self {
            connection,
            root,
            window,
            selection,
            targets,
            utf8,
            plain,
            incr,
            timestamp,
            multiple,
            property,
            acquired: 0,
            text: None,
        })
    }
    fn window(c: &RustConnection, root: u32) -> Result<u32, EIPError> {
        let window = c.generate_id().map_err(|_| unavailable())?;
        c.create_window(
            0,
            window,
            root,
            0,
            0,
            1,
            1,
            0,
            WindowClass::INPUT_ONLY,
            0,
            &CreateWindowAux::new().event_mask(EventMask::PROPERTY_CHANGE),
        )
        .map_err(|_| unavailable())?
        .check()
        .map_err(|_| unavailable())?;
        Ok(window)
    }
    fn run(mut self, commands: mpsc::Receiver<Command>) {
        loop {
            match commands.try_recv() {
                Ok(Command::Read(max, cancel, reply)) => {
                    let _ = reply.send(self.read(max, &cancel));
                }
                Ok(Command::Write(text, cancel, reply)) => {
                    let _ = reply.send(self.write(text, &cancel));
                }
                Err(mpsc::TryRecvError::Disconnected) => break,
                Err(mpsc::TryRecvError::Empty) => {}
            }
            // Bound each batch so a busy peer cannot starve close or commands.
            for _ in 0..64 {
                match self.connection.poll_for_event() {
                    Ok(Some(event)) => self.serve(&event),
                    Ok(None) => break,
                    Err(_) => return,
                }
            }
            std::thread::sleep(Duration::from_millis(2));
        }
    }
    fn serve(&mut self, event: &Event) {
        match event {
            Event::SelectionClear(e) if e.owner == self.window && e.selection == self.selection => {
                // A queued clear can precede our latest reacquisition. Only drop
                // the payload if this window is no longer the server's owner.
                let still_owned = self
                    .connection
                    .get_selection_owner(self.selection)
                    .ok()
                    .and_then(|cookie| cookie.reply().ok())
                    .is_some_and(|reply| reply.owner == self.window);
                if !still_owned {
                    self.text = None;
                }
            }
            Event::SelectionRequest(e)
                if e.owner == self.window && e.selection == self.selection =>
            {
                let property = if e.property == 0 {
                    e.target
                } else {
                    e.property
                };
                // A queued request from an earlier publication must not receive
                // the replacement payload, including through MULTIPLE.
                let success = request_is_current(e.time, self.acquired)
                    && if e.target == self.multiple {
                        self.convert_multiple(e.requestor, property).is_ok()
                    } else {
                        self.convert(e.requestor, e.target, property).is_ok()
                    };
                let reply = SelectionNotifyEvent {
                    response_type: SELECTION_NOTIFY_EVENT,
                    sequence: 0,
                    time: e.time,
                    requestor: e.requestor,
                    selection: e.selection,
                    target: e.target,
                    property: if success { property } else { 0 },
                };
                let _ = self
                    .connection
                    .send_event(false, e.requestor, EventMask::NO_EVENT, reply);
                let _ = self.connection.flush();
            }
            _ => {}
        }
    }
    fn convert_multiple(&self, window: u32, property: u32) -> Result<(), EIPError> {
        let p = self
            .connection
            .get_property(false, window, property, AtomEnum::ANY, 0, 128)
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?;
        if p.bytes_after != 0 || p.format != 32 {
            return Err(unavailable());
        }
        let mut pairs: Vec<u32> = p.value32().ok_or_else(unavailable)?.collect();
        if !pairs.len().is_multiple_of(2) {
            return Err(unavailable());
        }
        for pair in pairs.chunks_exact_mut(2) {
            if pair[0] == self.multiple
                || pair[1] == property
                || self.convert(window, pair[0], pair[1]).is_err()
            {
                pair[1] = 0;
            }
        }
        self.connection
            .change_property32(PropMode::REPLACE, window, property, p.type_, &pairs)
            .map_err(|_| unavailable())?
            .check()
            .map_err(|_| unavailable())
    }
    fn convert(&self, window: u32, target: u32, property: u32) -> Result<(), EIPError> {
        let text = self.text.as_ref().ok_or_else(unavailable)?;
        if property == 0 {
            return Err(unavailable());
        }
        let cookie = if target == self.targets {
            let mut targets = vec![
                self.targets,
                self.timestamp,
                self.multiple,
                self.utf8,
                self.plain,
            ];
            if text.chars().all(|c| u32::from(c) <= 255) {
                targets.push(AtomEnum::STRING.into());
            }
            self.connection.change_property32(
                PropMode::REPLACE,
                window,
                property,
                AtomEnum::ATOM,
                &targets,
            )
        } else if target == self.timestamp {
            self.connection.change_property32(
                PropMode::REPLACE,
                window,
                property,
                AtomEnum::INTEGER,
                &[self.acquired],
            )
        } else if target == self.utf8 || target == self.plain {
            self.connection.change_property8(
                PropMode::REPLACE,
                window,
                property,
                target,
                text.as_bytes(),
            )
        } else if target == u32::from(AtomEnum::STRING) && text.chars().all(|c| u32::from(c) <= 255)
        {
            let latin: Vec<u8> = text.chars().map(|c| c as u8).collect();
            self.connection
                .change_property8(PropMode::REPLACE, window, property, target, &latin)
        } else {
            return Err(unavailable());
        };
        cookie
            .map_err(|_| unavailable())?
            .check()
            .map_err(|_| unavailable())
    }
    fn next(&mut self, deadline: Instant, cancel: &AtomicBool) -> Result<Event, EIPError> {
        loop {
            if cancel.load(Ordering::Acquire) {
                return Err(error(
                    ErrorType::Cancelled,
                    "clipboard operation interrupted",
                ));
            }
            if Instant::now() >= deadline {
                return Err(error(ErrorType::Timeout, "clipboard selection timed out"));
            }
            if let Some(event) = self
                .connection
                .poll_for_event()
                .map_err(|_| unavailable())?
            {
                self.serve(&event);
                return Ok(event);
            }
            std::thread::sleep(Duration::from_millis(2));
        }
    }
    fn write(&mut self, text: String, cancel: &AtomicBool) -> Result<Effect, EIPError> {
        let deadline = Instant::now() + Duration::from_secs(5);
        self.connection
            .change_property8(
                PropMode::REPLACE,
                self.window,
                self.property,
                AtomEnum::STRING,
                &[],
            )
            .map_err(|_| unavailable())?
            .check()
            .map_err(|_| unavailable())?;
        let time = loop {
            if let Event::PropertyNotify(e) = self.next(deadline, cancel)?
                && e.window == self.window
                && e.atom == self.property
            {
                break e.time;
            }
        };
        if cancel.load(Ordering::Acquire) {
            return Ok(effect(ComputerEffect::NotExecuted));
        }
        // After issuing SetSelectionOwner, every failure carries mutation evidence.
        self.text = Some(text);
        self.acquired = time;
        let changed = self
            .connection
            .set_selection_owner(self.window, self.selection, time)
            .map_err(|_| unavailable())
            .and_then(|c| c.check().map_err(|_| unavailable()));
        if changed.is_err() {
            return Ok(effect(ComputerEffect::Unknown));
        }
        match self
            .connection
            .get_selection_owner(self.selection)
            .map_err(|_| unavailable())
            .and_then(|c| c.reply().map_err(|_| unavailable()))
        {
            Ok(reply) if reply.owner == self.window => Ok(effect(ComputerEffect::Executed)),
            _ => Ok(effect(ComputerEffect::Unknown)),
        }
    }
    fn read(
        &mut self,
        max: u32,
        cancel: &AtomicBool,
    ) -> Result<ComputerReadClipboardResult, EIPError> {
        let owner = self
            .connection
            .get_selection_owner(self.selection)
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?
            .owner;
        if owner == 0 {
            return Ok(empty(ComputerClipboardStatus::NoContent));
        }
        if owner == self.window {
            return text(self.text.clone().ok_or_else(unavailable)?, max);
        }
        let window = Self::window(&self.connection, self.root)?;
        let deadline = Instant::now() + Duration::from_secs(5);
        let result = self.read_external(window, max, deadline, cancel);
        let _ = self.connection.destroy_window(window);
        let _ = self.connection.flush();
        let result = result?;
        if self
            .connection
            .get_selection_owner(self.selection)
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?
            .owner
            != owner
        {
            return Err(error(
                ErrorType::Conflict,
                "clipboard owner changed during read",
            ));
        }
        Ok(result)
    }
    fn read_external(
        &mut self,
        window: u32,
        max: u32,
        deadline: Instant,
        cancel: &AtomicBool,
    ) -> Result<ComputerReadClipboardResult, EIPError> {
        let targets = self.receive(window, self.targets, deadline, cancel)?;
        let target = if let Some((kind, format, bytes)) = targets {
            if kind != u32::from(AtomEnum::ATOM) || format != 32 || bytes.len() > 4096 {
                return Err(unavailable());
            }
            let offered: Vec<u32> = bytes
                .chunks_exact(4)
                .map(|b| u32::from_ne_bytes(b.try_into().unwrap()))
                .collect();
            [self.utf8, self.plain, AtomEnum::STRING.into()]
                .into_iter()
                .find(|a| offered.contains(a))
        } else {
            Some(self.utf8)
        };
        let Some(target) = target else {
            return Ok(empty(ComputerClipboardStatus::NoText));
        };
        let Some((kind, format, bytes)) = self.receive(window, target, deadline, cancel)? else {
            return Ok(empty(ComputerClipboardStatus::NoText));
        };
        if kind != target || format != 8 {
            return Err(invalid_text());
        }
        let value = if target == u32::from(AtomEnum::STRING) {
            bytes.into_iter().map(char::from).collect()
        } else {
            String::from_utf8(bytes).map_err(|_| invalid_text())?
        };
        text(value, max)
    }
    fn receive(
        &mut self,
        window: u32,
        target: u32,
        deadline: Instant,
        cancel: &AtomicBool,
    ) -> Result<Option<(u32, u8, Vec<u8>)>, EIPError> {
        self.connection
            .delete_property(window, self.property)
            .map_err(|_| unavailable())?;
        self.connection
            .convert_selection(
                window,
                self.selection,
                target,
                self.property,
                x11rb::CURRENT_TIME,
            )
            .map_err(|_| unavailable())?;
        self.connection.flush().map_err(|_| unavailable())?;
        loop {
            if let Event::SelectionNotify(e) = self.next(deadline, cancel)?
                && e.requestor == window
                && e.selection == self.selection
                && e.target == target
            {
                if e.property == 0 {
                    return Ok(None);
                }
                if e.property == self.property {
                    break;
                }
            }
        }
        let p = self.property(window)?;
        if p.type_ != self.incr {
            return Ok(Some((p.type_, p.format, p.value)));
        }
        if p.format != 32
            || p.value.len() != 4
            || p.value32()
                .and_then(|mut v| v.next())
                .is_none_or(|n| n as usize > MAX_NATIVE_BYTES)
        {
            return Err(oversize());
        }
        let mut bytes = Vec::new();
        let mut kind = None;
        loop {
            if let Event::PropertyNotify(e) = self.next(deadline, cancel)? {
                if e.window != window || e.atom != self.property || e.state != Property::NEW_VALUE {
                    continue;
                }
                let p = self.property(window)?;
                if p.type_ == 0 {
                    continue;
                }
                if bytes.len() + p.value.len() > MAX_NATIVE_BYTES {
                    return Err(oversize());
                }
                if kind.is_some_and(|k| k != (p.type_, p.format)) {
                    return Err(invalid_text());
                }
                kind = Some((p.type_, p.format));
                if p.value.is_empty() {
                    return Ok(Some((p.type_, p.format, bytes)));
                }
                bytes.extend(p.value);
            }
        }
    }
    fn property(&self, window: u32) -> Result<GetPropertyReply, EIPError> {
        let p = self
            .connection
            .get_property(
                true,
                window,
                self.property,
                AtomEnum::ANY,
                0,
                (MAX_NATIVE_BYTES / 4) as u32,
            )
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?;
        if p.bytes_after != 0 {
            return Err(oversize());
        }
        self.connection.flush().map_err(|_| unavailable())?;
        Ok(p)
    }
}
impl Drop for State {
    fn drop(&mut self) {
        let _ = self.connection.destroy_window(self.window);
        let _ = self.connection.flush();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn clipboard() -> Clipboard {
        let (connection, screen) = x11rb::connect(None).unwrap();
        let root = connection.setup().roots[screen].root;
        Clipboard::start(Arc::new(connection), root).unwrap()
    }

    // Independent X client that can serve direct data, INCR chunks, or stall.
    struct ExternalOwner {
        stop: Arc<AtomicBool>,
        worker: Option<std::thread::JoinHandle<()>>,
    }
    impl ExternalOwner {
        fn start(payload: Option<Vec<u8>>, incremental: bool, stall: bool) -> Self {
            let (connection, screen) = x11rb::connect(None).unwrap();
            let root = connection.setup().roots[screen].root;
            let state = State::new(Arc::new(connection), root).unwrap();
            state
                .connection
                .set_selection_owner(state.window, state.selection, x11rb::CURRENT_TIME)
                .unwrap()
                .check()
                .unwrap();
            let stop = Arc::new(AtomicBool::new(false));
            let done = stop.clone();
            let worker = std::thread::spawn(move || {
                let mut transfer: Option<(u32, u32, usize)> = None;
                while !done.load(Ordering::Acquire) {
                    let Some(event) = state.connection.poll_for_event().unwrap() else {
                        std::thread::sleep(Duration::from_millis(2));
                        continue;
                    };
                    match event {
                        Event::SelectionRequest(e) => {
                            if e.target == state.targets {
                                let offered = if payload.is_some() {
                                    vec![state.utf8]
                                } else {
                                    vec![]
                                };
                                state
                                    .connection
                                    .change_property32(
                                        PropMode::REPLACE,
                                        e.requestor,
                                        e.property,
                                        AtomEnum::ATOM,
                                        &offered,
                                    )
                                    .unwrap();
                            } else if incremental {
                                state
                                    .connection
                                    .change_window_attributes(
                                        e.requestor,
                                        &ChangeWindowAttributesAux::new()
                                            .event_mask(EventMask::PROPERTY_CHANGE),
                                    )
                                    .unwrap();
                                // Deliberately understate the size to test the cumulative bound.
                                state
                                    .connection
                                    .change_property32(
                                        PropMode::REPLACE,
                                        e.requestor,
                                        e.property,
                                        state.incr,
                                        &[1],
                                    )
                                    .unwrap();
                                transfer = Some((e.requestor, e.property, 0));
                            } else {
                                state
                                    .connection
                                    .change_property8(
                                        PropMode::REPLACE,
                                        e.requestor,
                                        e.property,
                                        state.utf8,
                                        payload.as_deref().unwrap_or_default(),
                                    )
                                    .unwrap();
                            }
                            state
                                .connection
                                .send_event(
                                    false,
                                    e.requestor,
                                    EventMask::NO_EVENT,
                                    SelectionNotifyEvent {
                                        response_type: SELECTION_NOTIFY_EVENT,
                                        sequence: 0,
                                        time: e.time,
                                        requestor: e.requestor,
                                        selection: e.selection,
                                        target: e.target,
                                        property: e.property,
                                    },
                                )
                                .unwrap();
                            state.connection.flush().unwrap();
                        }
                        Event::PropertyNotify(e) if e.state == Property::DELETE && !stall => {
                            if let Some((window, property, offset)) = transfer
                                && e.window == window
                                && e.atom == property
                            {
                                let data = payload.as_deref().unwrap();
                                let end = (offset + 4096).min(data.len());
                                state
                                    .connection
                                    .change_property8(
                                        PropMode::REPLACE,
                                        window,
                                        property,
                                        state.utf8,
                                        &data[offset..end],
                                    )
                                    .unwrap();
                                state.connection.flush().unwrap();
                                transfer = (end != offset).then_some((window, property, end));
                            }
                        }
                        _ => {}
                    }
                }
            });
            Self {
                stop,
                worker: Some(worker),
            }
        }
    }
    impl Drop for ExternalOwner {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::Release);
            self.worker.take().unwrap().join().unwrap();
        }
    }

    #[test]
    #[ignore = "requires an isolated Xvfb DISPLAY"]
    fn clipboard_external_formats_and_incremental_bounds() {
        let reader = clipboard();
        for incremental in [false, true] {
            let value = "é中\r\n".repeat(1800);
            let owner = ExternalOwner::start(Some(value.clone().into_bytes()), incremental, false);
            assert_eq!(
                reader.read(16384, &|| false).unwrap().text.unwrap(),
                value.replace("\r\n", "\n")
            );
            assert_eq!(
                reader.read(1, &|| false).unwrap_err().data.error_type,
                ErrorType::QuotaExceeded
            );
            drop(owner);
        }
        let owner = ExternalOwner::start(None, false, false);
        assert_eq!(
            reader.read(16384, &|| false).unwrap().status,
            ComputerClipboardStatus::NoText
        );
        drop(owner);
        let owner = ExternalOwner::start(Some(vec![0xff]), false, false);
        assert_eq!(
            reader.read(16384, &|| false).unwrap_err().data.error_type,
            ErrorType::ProviderUnavailable
        );
        drop(owner);
        let owner = ExternalOwner::start(Some(vec![b'a'; MAX_NATIVE_BYTES + 4096]), true, false);
        assert_eq!(
            reader.read(16384, &|| false).unwrap_err().data.error_type,
            ErrorType::QuotaExceeded
        );
        drop(owner);
    }

    #[test]
    #[ignore = "requires an isolated Xvfb DISPLAY"]
    fn clipboard_incremental_cancel_and_deadline() {
        let reader = clipboard();
        let _owner = ExternalOwner::start(Some(vec![b'a']), true, true);
        let start = Instant::now();
        assert_eq!(
            reader
                .read(16384, &|| start.elapsed() > Duration::from_millis(50))
                .unwrap_err()
                .data
                .error_type,
            ErrorType::Cancelled
        );
        assert!(start.elapsed() < Duration::from_secs(2));
        let start = Instant::now();
        assert_eq!(
            reader.read(16384, &|| false).unwrap_err().data.error_type,
            ErrorType::Timeout
        );
        assert!(start.elapsed() < Duration::from_secs(7));
    }

    #[test]
    fn clipboard_request_timestamp_wraparound() {
        assert!(request_is_current(0, 100));
        assert!(request_is_current(100, 100));
        assert!(request_is_current(101, 100));
        assert!(!request_is_current(99, 100));
        assert!(request_is_current(10, u32::MAX - 10));
        assert!(!request_is_current(u32::MAX - 10, 10));
    }

    #[test]
    #[ignore = "requires an isolated Xvfb DISPLAY"]
    fn clipboard_stale_requests_do_not_receive_replacement() {
        fn next_event(connection: &RustConnection) -> Event {
            let deadline = Instant::now() + Duration::from_secs(2);
            loop {
                if let Some(event) = connection.poll_for_event().unwrap() {
                    return event;
                }
                assert!(Instant::now() < deadline, "missing selection event");
                std::thread::sleep(Duration::from_millis(1));
            }
        }
        let (connection, screen) = x11rb::connect(None).unwrap();
        let root = connection.setup().roots[screen].root;
        let mut state = State::new(Arc::new(connection), root).unwrap();
        let (other, _) = x11rb::connect(None).unwrap();
        let window = State::window(&other, root).unwrap();
        for multiple in [false, true] {
            state.write("old".into(), &AtomicBool::new(false)).unwrap();
            let old = state.acquired;
            let target = if multiple { state.multiple } else { state.utf8 };
            other.delete_property(window, state.plain).unwrap();
            if multiple {
                other
                    .change_property32(
                        PropMode::REPLACE,
                        window,
                        state.property,
                        AtomEnum::ATOM,
                        &[state.utf8, state.plain],
                    )
                    .unwrap();
            }
            other
                .convert_selection(window, state.selection, target, state.property, old)
                .unwrap()
                .check()
                .unwrap();
            // Retain the actual server request until after a newer publication,
            // reproducing a request queued behind write's timestamp notification.
            let request = loop {
                let event = next_event(&state.connection);
                if matches!(event, Event::SelectionRequest(_)) {
                    break event;
                }
                state.serve(&event);
            };
            let deadline = Instant::now() + Duration::from_secs(2);
            while state.acquired == old {
                assert!(Instant::now() < deadline);
                state.write("new".into(), &AtomicBool::new(false)).unwrap();
            }
            state.serve(&request);
            let notify = loop {
                if let Event::SelectionNotify(e) = next_event(&other) {
                    break e;
                }
            };
            assert_eq!(notify.property, 0);
            let property = if multiple {
                state.plain
            } else {
                state.property
            };
            let data = other
                .get_property(false, window, property, AtomEnum::ANY, 0, 64)
                .unwrap()
                .reply()
                .unwrap();
            assert_eq!(data.type_, 0);
            // Both the acquisition time and CURRENT_TIME still serve new content.
            for time in [state.acquired, x11rb::CURRENT_TIME] {
                let Event::SelectionRequest(mut current) = request.clone() else {
                    unreachable!()
                };
                current.time = time;
                state.serve(&Event::SelectionRequest(current));
                let notify = loop {
                    if let Event::SelectionNotify(e) = next_event(&other) {
                        break e;
                    }
                };
                assert_eq!(notify.property, state.property);
                let data = other
                    .get_property(true, window, property, state.utf8, 0, 64)
                    .unwrap()
                    .reply()
                    .unwrap();
                assert_eq!(data.value, b"new");
            }
        }
    }

    #[test]
    #[ignore = "requires an isolated Xvfb DISPLAY"]
    fn clipboard_delayed_clear_preserves_reacquired_payload() {
        let (connection, screen) = x11rb::connect(None).unwrap();
        let root = connection.setup().roots[screen].root;
        let mut state = State::new(Arc::new(connection), root).unwrap();
        state.write("old".into(), &AtomicBool::new(false)).unwrap();
        let (other, _) = x11rb::connect(None).unwrap();
        let window = State::window(&other, root).unwrap();
        other
            .set_selection_owner(window, state.selection, state.acquired)
            .unwrap()
            .check()
            .unwrap();
        assert_eq!(
            other
                .get_selection_owner(state.selection)
                .unwrap()
                .reply()
                .unwrap()
                .owner,
            window
        );
        state
            .connection
            .set_selection_owner(state.window, state.selection, state.acquired)
            .unwrap()
            .check()
            .unwrap();
        state.text = Some("new".into());
        assert_eq!(
            state
                .connection
                .get_selection_owner(state.selection)
                .unwrap()
                .reply()
                .unwrap()
                .owner,
            state.window
        );
        let mut cleared = false;
        while let Some(event) = state.connection.poll_for_event().unwrap() {
            cleared |= matches!(event, Event::SelectionClear(_));
            state.serve(&event);
        }
        assert!(cleared);
        assert_eq!(state.text.as_deref(), Some("new"));
    }

    #[test]
    #[ignore = "requires an isolated Xvfb DISPLAY and xclip"]
    fn clipboard_roundtrip_and_session_lifetime() {
        let first = clipboard();
        let second = clipboard();
        assert_eq!(
            first.read(16384, &|| false).unwrap().status,
            ComputerClipboardStatus::NoContent
        );
        let value = "Clipboard café 中文 🦀\nsecond line\n";
        assert_eq!(
            first.write(value, &|| false).unwrap().effect,
            ComputerEffect::Executed
        );
        assert_eq!(
            first.read(16384, &|| false).unwrap().text.as_deref(),
            Some(value)
        );
        assert_eq!(
            second.read(16384, &|| false).unwrap().text.as_deref(),
            Some(value)
        );
        let output = std::process::Command::new("xclip")
            .args(["-selection", "clipboard", "-out"])
            .output()
            .unwrap();
        assert!(output.status.success());
        assert_eq!(String::from_utf8(output.stdout).unwrap(), value);
        assert!(second.read(2, &|| false).is_err());
        assert_eq!(
            second.write("", &|| false).unwrap().effect,
            ComputerEffect::Executed
        );
        drop(first); // Closing an earlier owner must not clear the later owner's data.
        assert_eq!(
            second.read(16384, &|| false).unwrap().text.as_deref(),
            Some("")
        );
        let third = clipboard();
        assert_eq!(
            third.read(16384, &|| false).unwrap().text.as_deref(),
            Some("")
        );
        drop(second);
        assert_eq!(
            third.read(16384, &|| false).unwrap().status,
            ComputerClipboardStatus::NoContent
        );
    }
}
