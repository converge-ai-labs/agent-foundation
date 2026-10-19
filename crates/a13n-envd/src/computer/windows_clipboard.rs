//! Eager CF_UNICODETEXT publication. Handles stay on the native operation thread.
#![allow(
    unsafe_code,
    reason = "Win32 clipboard APIs transfer ownership of movable memory"
)]
use super::{clipboard::*, *};
use std::{
    mem::size_of,
    ptr::{null, null_mut},
};
use windows_sys::Win32::{
    Foundation::{GlobalFree, HWND},
    System::{DataExchange::*, Memory::*},
    UI::WindowsAndMessaging::*,
};
const UNICODE_TEXT: u32 = 13;

struct Window(HWND);
impl Window {
    fn new() -> Result<Self, EIPError> {
        let class: Vec<u16> = "STATIC\0".encode_utf16().collect();
        let hwnd = unsafe {
            CreateWindowExW(
                0,
                class.as_ptr(),
                null(),
                0,
                0,
                0,
                0,
                0,
                HWND_MESSAGE,
                null_mut(),
                null_mut(),
                null(),
            )
        };
        if hwnd.is_null() {
            Err(unavailable())
        } else {
            Ok(Self(hwnd))
        }
    }
}
impl Drop for Window {
    fn drop(&mut self) {
        unsafe {
            DestroyWindow(self.0);
        }
    }
}
struct Open(bool);
impl Open {
    fn new(window: HWND, interrupted: &dyn Fn() -> bool) -> Result<Self, EIPError> {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            check(interrupted)?;
            if unsafe { OpenClipboard(window) } != 0 {
                return Ok(Self(true));
            }
            if Instant::now() >= deadline {
                return Err(error(ErrorType::Timeout, "clipboard is busy"));
            }
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    fn close(&mut self) -> bool {
        if !self.0 {
            return true;
        }
        let closed = unsafe { CloseClipboard() } != 0;
        if closed {
            self.0 = false;
        }
        closed
    }
}
impl Drop for Open {
    fn drop(&mut self) {
        self.close();
    }
}
struct Memory(*mut std::ffi::c_void);
impl Drop for Memory {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                GlobalFree(self.0);
            }
        }
    }
}
fn unavailable() -> EIPError {
    error(
        ErrorType::ProviderUnavailable,
        "Windows clipboard is unavailable",
    )
}

pub(super) fn read(
    max: u32,
    interrupted: &dyn Fn() -> bool,
) -> Result<ComputerReadClipboardResult, EIPError> {
    read_with_readiness(max, interrupted, &super::windows::ready)
}

fn read_with_readiness(
    max: u32,
    interrupted: &dyn Fn() -> bool,
    ready: &dyn Fn() -> Result<(), EIPError>,
) -> Result<ComputerReadClipboardResult, EIPError> {
    ready()?;
    let mut opened = Open::new(null_mut(), interrupted)?;
    // Acquiring the clipboard after contention does not prove desktop readiness.
    ready()?;
    let result = unsafe {
        if IsClipboardFormatAvailable(UNICODE_TEXT) == 0 {
            Ok(empty(if CountClipboardFormats() == 0 {
                ComputerClipboardStatus::NoContent
            } else {
                ComputerClipboardStatus::NoText
            }))
        } else {
            // A delayed owner can block here. The Session retains and joins this
            // operation, and cancellation suppresses publication after it returns.
            let handle = GetClipboardData(UNICODE_TEXT);
            if handle.is_null() {
                return Err(unavailable());
            }
            let bytes = GlobalSize(handle);
            if bytes > MAX_NATIVE_BYTES {
                return Err(oversize());
            }
            if bytes < 2 || !bytes.is_multiple_of(2) {
                return Err(invalid_text());
            }
            let pointer = GlobalLock(handle).cast::<u16>();
            if pointer.is_null() {
                return Err(unavailable());
            }
            let data = std::slice::from_raw_parts(pointer, bytes / size_of::<u16>());
            // CF_UNICODETEXT ends at the first NUL. GlobalSize bounds the allocation,
            // not the text; bytes after the terminator need not be zero padding.
            let value = data
                .iter()
                .position(|v| *v == 0)
                .ok_or_else(invalid_text)
                .and_then(|end| String::from_utf16(&data[..end]).map_err(|_| invalid_text()));
            GlobalUnlock(handle);
            text(value?, max)
        }
    };
    if !opened.close() {
        return Err(unavailable());
    }
    check(interrupted)?;
    // Delayed rendering can wait on another process while the desktop becomes unavailable.
    ready()?;
    result
}

pub(super) fn write(value: &str, interrupted: &dyn Fn() -> bool) -> Result<Effect, EIPError> {
    write_with_readiness(value, interrupted, &super::windows::ready)
}

fn write_with_readiness(
    value: &str,
    interrupted: &dyn Fn() -> bool,
    ready: &dyn Fn() -> Result<(), EIPError>,
) -> Result<Effect, EIPError> {
    ready()?;
    let data: Vec<u16> = value
        .replace('\n', "\r\n")
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect();
    let mut memory = Memory(unsafe { GlobalAlloc(GMEM_MOVEABLE, data.len() * size_of::<u16>()) });
    if memory.0.is_null() {
        return Err(unavailable());
    }
    unsafe {
        let pointer = GlobalLock(memory.0).cast::<u16>();
        if pointer.is_null() {
            return Err(unavailable());
        }
        std::ptr::copy_nonoverlapping(data.as_ptr(), pointer, data.len());
        GlobalUnlock(memory.0);
    }
    let window = Window::new()?;
    let mut opened = Open::new(window.0, interrupted)?;
    check(interrupted)?;
    // Revalidate immediately before mutation, not only before the contention wait.
    ready()?;
    let result = unsafe {
        if EmptyClipboard() == 0 {
            effect(ComputerEffect::NotExecuted)
        } else if SetClipboardData(UNICODE_TEXT, memory.0).is_null() {
            effect(ComputerEffect::Partial)
        } else {
            memory.0 = null_mut(); // The system owns it only after successful publication.
            effect(ComputerEffect::Executed)
        }
    };
    Ok(Effect {
        cleanup_complete: opened.close(),
        ..result
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc,
    };
    use windows_sys::Win32::UI::Controls::{EM_GETSEL, EM_SETSEL};

    // Publish independent native data, including values the public write API rejects.
    fn publish(format: u32, data: &[u16]) {
        let window = Window::new().unwrap();
        let mut opened = Open::new(window.0, &|| false).unwrap();
        unsafe {
            assert_ne!(EmptyClipboard(), 0);
            let mut memory = Memory(GlobalAlloc(GMEM_MOVEABLE, std::mem::size_of_val(data)));
            assert!(!memory.0.is_null());
            let pointer = GlobalLock(memory.0).cast::<u16>();
            assert!(!pointer.is_null());
            std::ptr::copy_nonoverlapping(data.as_ptr(), pointer, data.len());
            GlobalUnlock(memory.0);
            assert!(!SetClipboardData(format, memory.0).is_null());
            memory.0 = null_mut();
        }
        assert!(opened.close());
    }

    #[test]
    #[ignore = "changes the native clipboard; run on an isolated desktop with --test-threads=1"]
    fn windows_clipboard_native_editor_roundtrip() {
        let value = "café 中文 🦀\nsecond line\n";
        // Eager data must remain usable after the publishing window and thread exit.
        std::thread::spawn(move || {
            let result = write(value, &|| false).unwrap();
            assert_eq!(result.effect, ComputerEffect::Executed);
            assert!(result.cleanup_complete);
        })
        .join()
        .unwrap();
        assert_eq!(
            read(MAX_TEXT_BYTES as u32, &|| false)
                .unwrap()
                .text
                .as_deref(),
            Some(value)
        );
        unsafe {
            let class: Vec<u16> = "STATIC\0".encode_utf16().collect();
            let parent = Window(CreateWindowExW(
                0,
                class.as_ptr(),
                null(),
                WS_OVERLAPPEDWINDOW,
                0,
                0,
                640,
                480,
                null_mut(),
                null_mut(),
                null_mut(),
                null(),
            ));
            assert!(!parent.0.is_null());
            let class: Vec<u16> = "EDIT\0".encode_utf16().collect();
            let editor = Window(CreateWindowExW(
                0,
                class.as_ptr(),
                null(),
                WS_CHILD | (ES_MULTILINE | ES_AUTOVSCROLL | ES_AUTOHSCROLL) as u32,
                0,
                0,
                320,
                200,
                parent.0,
                null_mut(),
                null_mut(),
                null(),
            ));
            assert!(!editor.0.is_null());
            // Exercise a real Windows edit control without stealing foreground focus.
            SendMessageW(editor.0, WM_PASTE, 0, 0);
            let mut buffer = vec![0_u16; GetWindowTextLengthW(editor.0) as usize + 1];
            let length = GetWindowTextW(editor.0, buffer.as_mut_ptr(), buffer.len() as i32);
            assert_eq!(
                String::from_utf16(&buffer[..length as usize]).unwrap(),
                value.replace('\n', "\r\n")
            );
            let copied = "from native editor: café 中文 🦀\r\nnext line";
            let text: Vec<u16> = copied.encode_utf16().chain(std::iter::once(0)).collect();
            assert_ne!(SetWindowTextW(editor.0, text.as_ptr()), 0);
            assert_eq!(GetWindowTextLengthW(editor.0) as usize, text.len() - 1);
            SendMessageW(editor.0, EM_SETSEL, 0, -1);
            let mut end = 0_u32;
            SendMessageW(editor.0, EM_GETSEL, 0, (&mut end as *mut u32) as isize);
            assert_eq!(end as usize, text.len() - 1);
            SendMessageW(editor.0, WM_COPY, 0, 0);
            assert_eq!(
                read(MAX_TEXT_BYTES as u32, &|| false).unwrap().text,
                Some(copied.replace("\r\n", "\n"))
            );
            // Compare our interpretation with a native consumer: storage following
            // CF_UNICODETEXT's terminator is not part of the clipboard string.
            for data in [[97, 0, 98, 0], [97, 0, 0, 0], [97, 0, 0xd800, 0]] {
                publish(UNICODE_TEXT, &data);
                assert_eq!(read(1, &|| false).unwrap().text.as_deref(), Some("a"));
                assert_ne!(SetWindowTextW(editor.0, [0_u16].as_ptr()), 0);
                SendMessageW(editor.0, WM_PASTE, 0, 0);
                let mut pasted = [0_u16; 8];
                let length = GetWindowTextW(editor.0, pasted.as_mut_ptr(), pasted.len() as i32);
                assert_eq!(length, 1);
                assert_eq!(pasted[0], 97);
            }
        }
    }

    #[test]
    #[ignore = "changes the native clipboard; run on an isolated desktop with --test-threads=1"]
    fn windows_clipboard_formats_unicode_and_limits() {
        {
            let window = Window::new().unwrap();
            let mut opened = Open::new(window.0, &|| false).unwrap();
            assert_ne!(unsafe { EmptyClipboard() }, 0);
            assert!(opened.close());
        }
        let result = read(MAX_TEXT_BYTES as u32, &|| false).unwrap();
        assert_eq!(result.status, ComputerClipboardStatus::NoContent);
        assert_eq!(result.text, None);
        let name: Vec<u16> = "a13n-envd-test-non-text\0".encode_utf16().collect();
        let format = unsafe { RegisterClipboardFormatW(name.as_ptr()) };
        assert_ne!(format, 0);
        publish(format, &[1, 2, 0]);
        let result = read(MAX_TEXT_BYTES as u32, &|| false).unwrap();
        assert_eq!(result.status, ComputerClipboardStatus::NoText);
        assert_eq!(result.text, None);

        for value in ["".to_string(), "a\nb\rc\n".to_string(), "é".repeat(8192)] {
            let result = write(&value, &|| false).unwrap();
            assert_eq!(result.effect, ComputerEffect::Executed);
            assert!(result.cleanup_complete);
            let result = read(MAX_TEXT_BYTES as u32, &|| false).unwrap();
            assert_eq!(result.status, ComputerClipboardStatus::Text);
            assert_eq!(result.text.as_deref(), Some(value.as_str()));
        }
        assert_eq!(
            read(1, &|| false).unwrap_err().data.error_type,
            ErrorType::QuotaExceeded
        );
        publish(UNICODE_TEXT, &[0xd800, 0]);
        let invalid = read(MAX_TEXT_BYTES as u32, &|| false).unwrap_err();
        assert_eq!(invalid.data.error_type, ErrorType::ProviderUnavailable);
        assert_eq!(invalid.data.retry_hint, crate::eip::RetryHint::Never);
        publish(UNICODE_TEXT, &vec![0; MAX_NATIVE_BYTES / 2 + 1]);
        assert_eq!(
            read(MAX_TEXT_BYTES as u32, &|| false)
                .unwrap_err()
                .data
                .error_type,
            ErrorType::QuotaExceeded
        );
        // Errors must release the clipboard lock so another native publication can succeed.
        assert!(write("after error", &|| false).unwrap().cleanup_complete);
    }

    #[test]
    #[ignore = "requires an interactive Windows session; run with --test-threads=1"]
    fn windows_clipboard_rejects_a_non_input_desktop() {
        use windows_sys::Win32::System::{StationsAndDesktops::*, Threading::GetCurrentThreadId};

        // Change only a new worker thread's desktop, never the user's input desktop.
        let (read_error, write_error) = std::thread::spawn(|| unsafe {
            let previous = GetThreadDesktop(GetCurrentThreadId());
            let name: Vec<u16> = format!("a13n-clipboard-test-{}\0", GetCurrentThreadId())
                .encode_utf16()
                .collect();
            let desktop = CreateDesktopW(
                name.as_ptr(),
                null(),
                null(),
                0,
                DESKTOP_CREATEWINDOW | DESKTOP_READOBJECTS | DESKTOP_WRITEOBJECTS,
                null(),
            );
            assert!(!desktop.is_null());
            let entered = SetThreadDesktop(desktop);
            if entered == 0 {
                CloseDesktop(desktop);
                panic!("cannot enter test desktop");
            }
            let read_error = read(MAX_TEXT_BYTES as u32, &|| false).err();
            let write_error = write("must not publish", &|| false).err();
            assert_ne!(SetThreadDesktop(previous), 0);
            assert_ne!(CloseDesktop(desktop), 0);
            (read_error.unwrap(), write_error.unwrap())
        })
        .join()
        .unwrap();
        for error in [read_error, write_error] {
            assert_eq!(error.data.error_type, ErrorType::ProviderUnavailable);
            assert_eq!(
                error.data.safe_detail.as_deref(),
                Some("computer_windows_desktop_unavailable")
            );
        }
    }

    #[test]
    #[ignore = "changes the native clipboard; run on an isolated desktop with --test-threads=1"]
    fn windows_clipboard_rechecks_readiness_after_contention() {
        for writing in [false, true] {
            write("must remain unchanged", &|| false).unwrap();
            let window = Window::new().unwrap();
            let mut opened = Open::new(window.0, &|| false).unwrap();
            let available = AtomicBool::new(true);
            let (waiting, observed_wait) = mpsc::channel();
            std::thread::scope(|scope| {
                let worker = scope.spawn(|| {
                    let attempts = AtomicUsize::new(0);
                    let interrupted = || {
                        // The second attempt proves the worker encountered the held clipboard.
                        if attempts.fetch_add(1, Ordering::Relaxed) == 1 {
                            waiting.send(()).unwrap();
                        }
                        false
                    };
                    // Inject only readiness loss, keeping native contention and access real.
                    // Locking the workstation would disrupt the user's interactive session.
                    let ready = || {
                        super::super::windows::ready()?;
                        if available.load(Ordering::Acquire) {
                            Ok(())
                        } else {
                            Err(unavailable())
                        }
                    };
                    if writing {
                        write_with_readiness("must not publish", &interrupted, &ready).err()
                    } else {
                        read_with_readiness(MAX_TEXT_BYTES as u32, &interrupted, &ready).err()
                    }
                });
                observed_wait.recv_timeout(Duration::from_secs(2)).unwrap();
                available.store(false, Ordering::Release);
                assert!(opened.close());
                assert_eq!(
                    worker.join().unwrap().unwrap().data.error_type,
                    ErrorType::ProviderUnavailable
                );
            });
            // Failed writes cannot clear/publish; both paths must release their native lock.
            assert_eq!(
                read(MAX_TEXT_BYTES as u32, &|| false)
                    .unwrap()
                    .text
                    .as_deref(),
                Some("must remain unchanged")
            );
        }
    }

    #[test]
    #[ignore = "changes the native clipboard; run on an isolated desktop with --test-threads=1"]
    fn windows_clipboard_rechecks_readiness_before_returning_text() {
        write("must not be returned", &|| false).unwrap();
        let checks = AtomicUsize::new(0);
        let result = read_with_readiness(MAX_TEXT_BYTES as u32, &|| false, &|| {
            super::super::windows::ready()?;
            // Ready at entry and acquisition, unavailable after native retrieval.
            if checks.fetch_add(1, Ordering::Relaxed) < 2 {
                Ok(())
            } else {
                Err(unavailable())
            }
        });
        assert_eq!(
            result.unwrap_err().data.error_type,
            ErrorType::ProviderUnavailable
        );
        assert_eq!(checks.load(Ordering::Relaxed), 3);
        assert!(
            write("after rejected read", &|| false)
                .unwrap()
                .cleanup_complete
        );
    }

    #[test]
    #[ignore = "changes the native clipboard; run on an isolated desktop with --test-threads=1"]
    fn windows_clipboard_contention_retry_cancel_and_timeout() {
        write("unchanged", &|| false).unwrap();
        let window = Window::new().unwrap();
        let mut opened = Open::new(window.0, &|| false).unwrap();
        let (waiting, observed_wait) = mpsc::channel();
        let reader = std::thread::spawn(move || {
            let attempts = AtomicUsize::new(0);
            read(MAX_TEXT_BYTES as u32, &|| {
                if attempts.fetch_add(1, Ordering::Relaxed) == 1 {
                    waiting.send(()).unwrap();
                }
                false
            })
        });
        observed_wait.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(opened.close());
        assert_eq!(
            reader.join().unwrap().unwrap().text.as_deref(),
            Some("unchanged")
        );

        let mut opened = Open::new(window.0, &|| false).unwrap();
        let cancelled = std::thread::spawn(|| {
            let deadline = Instant::now() + Duration::from_millis(50);
            write("cancelled write", &|| Instant::now() >= deadline)
                .err()
                .unwrap()
        })
        .join()
        .unwrap();
        assert_eq!(cancelled.data.error_type, ErrorType::Cancelled);
        let started = Instant::now();
        let timed_out = std::thread::spawn(|| write("timed out write", &|| false).err().unwrap())
            .join()
            .unwrap();
        assert_eq!(timed_out.data.error_type, ErrorType::Timeout);
        assert!(started.elapsed() >= Duration::from_secs(5));
        assert!(opened.close());
        assert_eq!(
            read(MAX_TEXT_BYTES as u32, &|| false)
                .unwrap()
                .text
                .as_deref(),
            Some("unchanged")
        );
        assert!(
            write("after contention", &|| false)
                .unwrap()
                .cleanup_complete
        );
    }
}
