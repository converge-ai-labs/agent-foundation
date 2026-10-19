//! Session-owned CLIPBOARD service on the Session's existing X connection.
use super::*;
use crate::filesystem::StagingReservation;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
    mpsc,
};
use x11rb::{protocol::Event, wrapper::ConnectionExt as _};

const NATIVE_LIMIT: usize = 128 * 1024;
const METADATA_LIMIT: usize = 4096;
const ACQUISITION_TIME: Duration = Duration::from_secs(5);
const EVENT_BATCH: usize = 64;

pub(super) struct Worker {
    sender: mpsc::SyncSender<Request>,
    stop: Arc<AtomicBool>,
    thread: Mutex<Option<std::thread::JoinHandle<bool>>>,
    cleanup: OnceLock<bool>,
}
struct Request {
    action: Action,
    cancelled: Arc<AtomicBool>,
    reply: mpsc::SyncSender<Result<Response, EIPError>>,
}
enum Action {
    Read(u32),
    Write(String, StagingReservation),
}
enum Response {
    Read(ComputerReadClipboardResult),
    Write(Effect),
}
struct Publication {
    text: String,
    _reservation: StagingReservation,
}
struct Selection {
    desktop: Arc<Desktop>,
    window: u32,
    clipboard: u32,
    targets: u32,
    utf8: u32,
    plain: u32,
    timestamp_atom: u32,
    incr: u32,
    property: u32,
    timestamp: u32,
    publication: Option<Publication>,
    stop: Arc<AtomicBool>,
}

fn quota() -> EIPError {
    error(
        ErrorType::QuotaExceeded,
        "clipboard exceeds its text or metadata limit",
    )
}
fn cancelled() -> EIPError {
    error(ErrorType::Cancelled, "clipboard operation cancelled")
}

impl Worker {
    pub(super) fn start(desktop: Arc<Desktop>) -> Result<Self, EIPError> {
        let stop = Arc::new(AtomicBool::new(false));
        let mut selection = Selection::new(desktop, stop.clone())?;
        let (sender, receiver) = mpsc::sync_channel::<Request>(1);
        let thread = std::thread::Builder::new()
            .name("x11-clipboard".into())
            .spawn(move || {
                while !selection.stop.load(Ordering::Acquire) {
                    match receiver.try_recv() {
                        Ok(request) => {
                            let result = match request.action {
                                Action::Read(max) => {
                                    selection.read(max, &request.cancelled).map(Response::Read)
                                }
                                Action::Write(text, reservation) => selection
                                    .write(text, reservation, &request.cancelled)
                                    .map(Response::Write),
                            };
                            let _ = request.reply.send(result);
                        }
                        Err(mpsc::TryRecvError::Disconnected) => break,
                        Err(mpsc::TryRecvError::Empty) => {}
                    }
                    if selection.events().is_err() {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(2));
                }
                selection.cleanup()
            })
            .map_err(|_| unavailable())?;
        Ok(Self {
            sender,
            stop,
            thread: Mutex::new(Some(thread)),
            cleanup: OnceLock::new(),
        })
    }

    fn call(&self, action: Action, interrupted: &dyn Fn() -> bool) -> Result<Response, EIPError> {
        // Active API calls serialize within this native worker process. Passive serving never holds this gate.
        static ADMISSION: Mutex<()> = Mutex::new(());
        let _gate = loop {
            if interrupted() || self.stop.load(Ordering::Acquire) {
                return Err(cancelled());
            }
            match ADMISSION.try_lock() {
                Ok(guard) => break guard,
                Err(std::sync::TryLockError::WouldBlock) => {
                    std::thread::sleep(Duration::from_millis(2))
                }
                Err(_) => return Err(unavailable()),
            }
        };
        let cancel = Arc::new(AtomicBool::new(false));
        let (reply, receive) = mpsc::sync_channel(1);
        self.sender
            .send(Request {
                action,
                cancelled: cancel.clone(),
                reply,
            })
            .map_err(|_| unavailable())?;
        loop {
            if interrupted() {
                cancel.store(true, Ordering::Release);
            }
            match receive.recv_timeout(Duration::from_millis(2)) {
                Ok(result) => return result,
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(_) => {
                    return Err(error(
                        ErrorType::UnknownOutcome,
                        "clipboard worker ended without evidence",
                    ));
                }
            }
        }
    }
    pub(super) fn read(
        &self,
        max: u32,
        interrupted: &dyn Fn() -> bool,
    ) -> Result<ComputerReadClipboardResult, EIPError> {
        match self.call(Action::Read(max), interrupted)? {
            Response::Read(value) => Ok(value),
            _ => unreachable!(),
        }
    }
    pub(super) fn write(
        &self,
        text: String,
        reservation: StagingReservation,
        interrupted: &dyn Fn() -> bool,
    ) -> Result<Effect, EIPError> {
        // A disappeared native worker can have published before losing its response.
        match self.call(Action::Write(text, reservation), interrupted) {
            Ok(Response::Write(value)) => Ok(value),
            Ok(_) => unreachable!(),
            Err(error) if error.data.error_type == ErrorType::UnknownOutcome => Ok(Effect {
                effect: ComputerEffect::Unknown,
                cleanup_complete: false,
            }),
            Err(error) => Err(error),
        }
    }
    pub(super) fn close(&self) -> bool {
        self.stop.store(true, Ordering::Release);
        *self.cleanup.get_or_init(|| {
            self.thread
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .take()
                .is_none_or(|thread| thread.join().unwrap_or(false))
        })
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

impl Selection {
    fn new(desktop: Arc<Desktop>, stop: Arc<AtomicBool>) -> Result<Self, EIPError> {
        let c = &desktop.connection;
        let atom = |name: &[u8]| -> Result<u32, EIPError> {
            Ok(c.intern_atom(false, name)
                .map_err(|_| unavailable())?
                .reply()
                .map_err(|_| unavailable())?
                .atom)
        };
        let clipboard = atom(b"CLIPBOARD")?;
        let targets = atom(b"TARGETS")?;
        let utf8 = atom(b"UTF8_STRING")?;
        let plain = atom(b"text/plain;charset=utf-8")?;
        let timestamp_atom = atom(b"TIMESTAMP")?;
        let incr = atom(b"INCR")?;
        let property = atom(b"A13N_CLIPBOARD")?;
        let window = Self::window(&desktop)?;
        Ok(Self {
            desktop,
            window,
            clipboard,
            targets,
            utf8,
            plain,
            timestamp_atom,
            incr,
            property,
            timestamp: 0,
            publication: None,
            stop,
        })
    }
    fn window(desktop: &Desktop) -> Result<u32, EIPError> {
        let c = &desktop.connection;
        let window = c.generate_id().map_err(|_| unavailable())?;
        c.create_window(
            0,
            window,
            desktop.root(),
            0,
            0,
            1,
            1,
            0,
            xproto::WindowClass::INPUT_ONLY,
            0,
            &xproto::CreateWindowAux::new().event_mask(xproto::EventMask::PROPERTY_CHANGE),
        )
        .map_err(|_| unavailable())?
        .check()
        .map_err(|_| unavailable())?;
        Ok(window)
    }
    fn check(&self, cancel: &AtomicBool, deadline: Instant) -> Result<(), EIPError> {
        if cancel.load(Ordering::Acquire) || self.stop.load(Ordering::Acquire) {
            return Err(cancelled());
        }
        if Instant::now() >= deadline {
            return Err(error(ErrorType::Timeout, "clipboard acquisition timed out"));
        }
        Ok(())
    }
    fn owner(&self) -> Result<u32, EIPError> {
        Ok(self
            .desktop
            .connection
            .get_selection_owner(self.clipboard)
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?
            .owner)
    }
    fn notify(&self, r: xproto::SelectionRequestEvent, property: u32) -> Result<(), EIPError> {
        self.desktop
            .connection
            .send_event(
                false,
                r.requestor,
                xproto::EventMask::NO_EVENT,
                xproto::SelectionNotifyEvent {
                    response_type: xproto::SELECTION_NOTIFY_EVENT,
                    sequence: 0,
                    time: r.time,
                    requestor: r.requestor,
                    selection: r.selection,
                    target: r.target,
                    property,
                },
            )
            .map_err(|_| unavailable())?
            .check()
            .map_err(|_| unavailable())
    }
    fn serve(&self, r: xproto::SelectionRequestEvent) -> Result<(), EIPError> {
        let Some(publication) = &self.publication else {
            return self.notify(r, 0);
        };
        if r.owner != self.window
            || r.selection != self.clipboard
            || (r.time != 0 && r.time.wrapping_sub(self.timestamp) > i32::MAX as u32)
        {
            return self.notify(r, 0);
        }
        let c = &self.desktop.connection;
        let property = if r.property == 0 {
            r.target
        } else {
            r.property
        };
        // The 16 KiB public bound fits one core X11 property; outgoing INCR is unnecessary.
        let changed = if r.target == self.targets {
            c.change_property32(
                xproto::PropMode::REPLACE,
                r.requestor,
                property,
                xproto::AtomEnum::ATOM,
                &[self.targets, self.utf8, self.plain, self.timestamp_atom],
            )
        } else if r.target == self.timestamp_atom {
            c.change_property32(
                xproto::PropMode::REPLACE,
                r.requestor,
                property,
                xproto::AtomEnum::INTEGER,
                &[self.timestamp],
            )
        } else if r.target == self.utf8 || r.target == self.plain {
            c.change_property8(
                xproto::PropMode::REPLACE,
                r.requestor,
                property,
                r.target,
                publication.text.as_bytes(),
            )
        } else {
            return self.notify(r, 0);
        };
        changed
            .map_err(|_| unavailable())?
            .check()
            .map_err(|_| unavailable())?;
        self.notify(r, property)
    }
    fn event(&mut self, event: Event) {
        match event {
            Event::SelectionRequest(request) => {
                let _ = self.serve(request);
            } // Requestors can disappear independently.
            Event::SelectionClear(event)
                if event.owner == self.window && event.selection == self.clipboard =>
            {
                // A delayed clear from an earlier publication must not drop a newer one.
                if self.owner().is_ok_and(|owner| owner != self.window) {
                    self.publication = None;
                }
            }
            _ => {}
        }
    }
    fn events(&mut self) -> Result<(), EIPError> {
        for _ in 0..EVENT_BATCH {
            match self
                .desktop
                .connection
                .poll_for_event()
                .map_err(|_| unavailable())?
            {
                Some(event) => self.event(event),
                None => break,
            }
        }
        Ok(())
    }
    fn write(
        &mut self,
        text: String,
        reservation: StagingReservation,
        cancel: &AtomicBool,
    ) -> Result<Effect, EIPError> {
        let deadline = Instant::now() + ACQUISITION_TIME;
        self.check(cancel, deadline)?;
        // Obtain a fresh server timestamp without changing the clipboard.
        self.desktop
            .connection
            .change_property8(
                xproto::PropMode::REPLACE,
                self.window,
                self.timestamp_atom,
                self.utf8,
                b"time",
            )
            .map_err(|_| unavailable())?
            .check()
            .map_err(|_| unavailable())?;
        let timestamp = loop {
            self.check(cancel, deadline)?;
            match self
                .desktop
                .connection
                .poll_for_event()
                .map_err(|_| unavailable())?
            {
                Some(Event::PropertyNotify(e))
                    if e.window == self.window && e.atom == self.timestamp_atom =>
                {
                    break e.time;
                }
                Some(event) => self.event(event),
                None => std::thread::sleep(Duration::from_millis(2)),
            }
        };
        self.check(cancel, deadline)?;
        self.timestamp = timestamp;
        // Keep the payload and its reservation even if acquisition's acknowledgement is lost.
        self.publication = Some(Publication {
            text,
            _reservation: reservation,
        });
        let acquired = self
            .desktop
            .connection
            .set_selection_owner(self.window, self.clipboard, self.timestamp)
            .ok()
            .is_some_and(|cookie| cookie.check().is_ok())
            && self.owner().is_ok_and(|owner| owner == self.window);
        Ok(Effect {
            effect: if acquired {
                ComputerEffect::Executed
            } else {
                ComputerEffect::Unknown
            },
            cleanup_complete: true,
        })
    }
    fn read(
        &mut self,
        max: u32,
        cancel: &AtomicBool,
    ) -> Result<ComputerReadClipboardResult, EIPError> {
        let deadline = Instant::now() + ACQUISITION_TIME;
        self.check(cancel, deadline)?;
        let owner = self.owner()?;
        if owner == 0 {
            return Ok(ComputerReadClipboardResult {
                status: ComputerClipboardStatus::NoContent,
                text: None,
            });
        }
        let requestor = Self::window(&self.desktop)?;
        let result = self.read_value(requestor, owner, max, cancel, deadline);
        let clean = self
            .desktop
            .connection
            .destroy_window(requestor)
            .ok()
            .is_some_and(|cookie| cookie.check().is_ok());
        if !clean {
            return Err(unavailable());
        }
        self.check(cancel, deadline)?;
        result
    }
    fn read_value(
        &mut self,
        requestor: u32,
        owner: u32,
        max: u32,
        cancel: &AtomicBool,
        deadline: Instant,
    ) -> Result<ComputerReadClipboardResult, EIPError> {
        let targets = self.convert(
            requestor,
            self.targets,
            xproto::AtomEnum::ATOM.into(),
            32,
            METADATA_LIMIT,
            cancel,
            deadline,
        )?;
        let Some(targets) = targets else {
            return Err(unsupported("clipboard owner did not provide TARGETS"));
        };
        let targets: Vec<u32> = targets
            .chunks_exact(4)
            .map(|v| u32::from_ne_bytes(v.try_into().expect("four bytes")))
            .collect();
        let target = [self.utf8, self.plain, u32::from(xproto::AtomEnum::STRING)]
            .into_iter()
            .find(|target| targets.contains(target));
        let value = match target {
            None => ComputerReadClipboardResult {
                status: ComputerClipboardStatus::NoText,
                text: None,
            },
            Some(target) => {
                let bytes = self
                    .convert(requestor, target, target, 8, NATIVE_LIMIT, cancel, deadline)?
                    .ok_or_else(|| {
                        error(ErrorType::Conflict, "clipboard text conversion was refused")
                    })?;
                let text = if target == u32::from(xproto::AtomEnum::STRING) {
                    bytes.into_iter().map(char::from).collect::<String>()
                } else {
                    String::from_utf8(bytes).map_err(|_| {
                        error(ErrorType::InvalidParams, "clipboard contains invalid UTF-8")
                    })?
                };
                if text.contains('\0') {
                    return Err(error(
                        ErrorType::InvalidParams,
                        "clipboard text contains NUL",
                    ));
                }
                let text = text.replace("\r\n", "\n");
                if text.len() > max as usize {
                    return Err(quota());
                }
                ComputerReadClipboardResult {
                    status: ComputerClipboardStatus::Text,
                    text: Some(text),
                }
            }
        };
        if self.owner()? != owner {
            return Err(error(
                ErrorType::Conflict,
                "clipboard owner changed during read",
            ));
        }
        Ok(value)
    }
    #[allow(clippy::too_many_arguments, reason = "bounded X11 conversion state")]
    fn convert(
        &mut self,
        requestor: u32,
        target: u32,
        expected: u32,
        format: u8,
        limit: usize,
        cancel: &AtomicBool,
        deadline: Instant,
    ) -> Result<Option<Vec<u8>>, EIPError> {
        self.desktop
            .connection
            .delete_property(requestor, self.property)
            .map_err(|_| unavailable())?
            .check()
            .map_err(|_| unavailable())?;
        self.desktop
            .connection
            .convert_selection(
                requestor,
                self.clipboard,
                target,
                self.property,
                x11rb::CURRENT_TIME,
            )
            .map_err(|_| unavailable())?
            .check()
            .map_err(|_| unavailable())?;
        let mut incremental = false;
        let mut notified = false;
        let mut bytes = Vec::new();
        loop {
            self.check(cancel, deadline)?;
            let event = self
                .desktop
                .connection
                .poll_for_event()
                .map_err(|_| unavailable())?;
            let fetch = match event {
                Some(Event::SelectionNotify(e))
                    if e.requestor == requestor
                        && e.selection == self.clipboard
                        && e.target == target
                        && !notified =>
                {
                    if e.property == 0 {
                        return Ok(None);
                    }
                    if e.property != self.property {
                        return Err(unsupported("unexpected clipboard property"));
                    }
                    notified = true;
                    true
                }
                Some(Event::PropertyNotify(e))
                    if incremental
                        && e.window == requestor
                        && e.atom == self.property
                        && e.state == xproto::Property::NEW_VALUE =>
                {
                    true
                }
                Some(event) => {
                    self.event(event);
                    false
                }
                None => {
                    std::thread::sleep(Duration::from_millis(2));
                    false
                }
            };
            if !fetch {
                continue;
            }
            let reply = self
                .desktop
                .connection
                .get_property(
                    true,
                    requestor,
                    self.property,
                    xproto::AtomEnum::ANY,
                    0,
                    ((limit.saturating_sub(bytes.len()) + 3) / 4) as u32,
                )
                .map_err(|_| unavailable())?
                .reply()
                .map_err(|_| unavailable())?;
            if reply.bytes_after != 0 || bytes.len() + reply.value.len() > limit {
                return Err(quota());
            }
            if !incremental && reply.type_ == self.incr {
                if reply.format != 32 || reply.value.len() != 4 {
                    return Err(unsupported("invalid clipboard INCR header"));
                }
                if u32::from_ne_bytes(reply.value[..4].try_into().expect("four bytes")) as usize
                    > limit
                {
                    return Err(quota());
                }
                incremental = true;
                continue;
            }
            if reply.type_ != expected || reply.format != format {
                return Err(unsupported("invalid clipboard text representation"));
            }
            let complete = !incremental || reply.value.is_empty();
            bytes.extend(reply.value);
            if complete {
                return Ok(Some(bytes));
            }
        }
    }
    fn cleanup(&mut self) -> bool {
        self.publication = None;
        let destroyed = self
            .desktop
            .connection
            .destroy_window(self.window)
            .ok()
            .is_some_and(|cookie| cookie.check().is_ok());
        if !destroyed {
            return false;
        }
        // The checked destroy fences future conversions; reject requests already queued before it.
        for _ in 0..1024 {
            match self.desktop.connection.poll_for_event() {
                Ok(Some(Event::SelectionRequest(request))) => {
                    let _ = self.notify(request, 0);
                }
                Ok(Some(_)) => {}
                Ok(None) => return true,
                Err(_) => return false,
            }
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repeated_close_preserves_native_cleanup_outcome() {
        for expected in [false, true] {
            let (sender, _receiver) = mpsc::sync_channel(1);
            let worker = Worker {
                sender,
                stop: Arc::new(AtomicBool::new(false)),
                thread: Mutex::new(Some(std::thread::spawn(move || expected))),
                cleanup: OnceLock::new(),
            };
            assert_eq!(worker.close(), expected);
            assert_eq!(worker.close(), expected);
        }
    }
}
