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
    super::windows::ready()?;
    let mut opened = Open::new(null_mut(), interrupted)?;
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
            if bytes < 2 || bytes % 2 != 0 {
                return Err(invalid_text());
            }
            let pointer = GlobalLock(handle).cast::<u16>();
            if pointer.is_null() {
                return Err(unavailable());
            }
            let data = std::slice::from_raw_parts(pointer, bytes / size_of::<u16>());
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
    result
}

pub(super) fn write(value: &str, interrupted: &dyn Fn() -> bool) -> Result<Effect, EIPError> {
    super::windows::ready()?;
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
