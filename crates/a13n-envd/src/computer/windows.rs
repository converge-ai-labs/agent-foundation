//! Native Windows shared-desktop capture and bounded input. No elevation or clipboard.
#![allow(
    unsafe_code,
    reason = "Win32 desktop APIs require scoped native handles and input buffers"
)]

use super::*;
use image::{DynamicImage, RgbImage, codecs::jpeg::JpegEncoder, imageops::FilterType};
use std::{
    mem::size_of,
    ptr::{null, null_mut},
};
use windows_sys::Win32::{
    Foundation::{LPARAM, RECT},
    Graphics::Gdi::*,
    System::{
        RemoteDesktop::*,
        StationsAndDesktops::{GetThreadDesktop, GetUserObjectInformationW, UOI_IO},
        Threading::{GetCurrentProcessId, GetCurrentThreadId},
    },
    UI::{HiDpi::*, Input::KeyboardAndMouse::*, WindowsAndMessaging::*},
};
use windows_sys::core::BOOL;

#[derive(Default)]
pub(super) struct Backend {}

fn unavailable() -> EIPError {
    reason(
        ErrorType::ProviderUnavailable,
        "computer_windows_desktop_unavailable",
        "Windows desktop unavailable; start envd in the intended signed-in, unlocked interactive user session, not a service or disconnected session",
    )
}

fn reason(kind: ErrorType, detail: &str, message: &str) -> EIPError {
    let mut e = error(kind, message);
    e.data.safe_detail = Some(detail.to_owned());
    e
}

fn session_ready(info: &WTSINFOEXW) -> bool {
    // UOI_IO can remain true beneath the Windows lock-screen curtain.
    info.Level == 1
        && unsafe {
            let state = &info.Data.WTSInfoExLevel1;
            state.SessionState == WTSActive && state.SessionFlags == WTS_SESSIONSTATE_UNLOCK as i32
        }
}

// A successful OpenInputDesktop is not sufficient: it also succeeds while disconnected.
fn ready() -> Result<(), EIPError> {
    unsafe {
        let mut session = 0;
        if ProcessIdToSessionId(GetCurrentProcessId(), &mut session) == 0 || session == 0 {
            return Err(unavailable());
        }
        let mut buffer = null_mut();
        let mut bytes = 0;
        let queried = WTSQuerySessionInformationW(
            null_mut(),
            session,
            WTSSessionInfoEx,
            &mut buffer,
            &mut bytes,
        );
        let active = queried != 0
            && !buffer.is_null()
            && bytes as usize >= size_of::<WTSINFOEXW>()
            && session_ready(&*buffer.cast::<WTSINFOEXW>());
        if !buffer.is_null() {
            WTSFreeMemory(buffer.cast());
        }
        let desktop = GetThreadDesktop(GetCurrentThreadId());
        let mut receives_input: BOOL = 0;
        if !active
            || desktop.is_null()
            || GetUserObjectInformationW(
                desktop,
                UOI_IO,
                (&mut receives_input as *mut BOOL).cast(),
                size_of::<BOOL>() as u32,
                null_mut(),
            ) == 0
            || receives_input == 0
        {
            return Err(unavailable());
        }
    }
    Ok(())
}

struct Dpi(DPI_AWARENESS_CONTEXT);
impl Dpi {
    fn enter() -> Result<Self, EIPError> {
        let previous =
            unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
        if previous.is_null() {
            Err(unavailable())
        } else {
            Ok(Self(previous))
        }
    }
}
impl Drop for Dpi {
    fn drop(&mut self) {
        unsafe {
            SetThreadDpiAwarenessContext(self.0);
        }
    }
}

unsafe extern "system" fn monitor_callback(
    handle: HMONITOR,
    _: HDC,
    _: *mut RECT,
    data: LPARAM,
) -> BOOL {
    let monitors = unsafe { &mut *(data as *mut Vec<(Geometry, bool)>) };
    let mut info = MONITORINFOEXW::default();
    info.monitorInfo.cbSize = size_of::<MONITORINFOEXW>() as u32;
    if unsafe { GetMonitorInfoW(handle, &mut info.monitorInfo) } == 0 {
        return 0;
    }
    let r = info.monitorInfo.rcMonitor;
    let length = info
        .szDevice
        .iter()
        .position(|c| *c == 0)
        .unwrap_or(info.szDevice.len());
    monitors.push((
        Geometry {
            target_id: String::from_utf16_lossy(&info.szDevice[..length]),
            x: f64::from(r.left),
            y: f64::from(r.top),
            width: f64::from(r.right - r.left),
            height: f64::from(r.bottom - r.top),
        },
        info.monitorInfo.dwFlags & MONITORINFOF_PRIMARY != 0,
    ));
    1
}

fn monitors() -> Result<Vec<(Geometry, bool)>, EIPError> {
    let mut monitors = Vec::new();
    if unsafe {
        EnumDisplayMonitors(
            null_mut(),
            null(),
            Some(monitor_callback),
            (&mut monitors as *mut Vec<(Geometry, bool)>) as LPARAM,
        )
    } == 0
        || monitors.is_empty()
    {
        return Err(unavailable());
    }
    Ok(monitors)
}

fn select(target: Option<&str>) -> Result<Geometry, EIPError> {
    monitors()?
        .into_iter()
        .find(|(g, primary)| target.map_or(*primary, |id| id == g.target_id))
        .map(|(g, _)| g)
        .ok_or_else(|| {
            error(
                ErrorType::Conflict,
                "display target changed; capture a fresh image",
            )
        })
}

// Handles are always destroyed in reverse selection order, including allocation failures.
struct Bitmap {
    screen: HDC,
    memory: HDC,
    bitmap: HBITMAP,
    previous: HGDIOBJ,
}
impl Drop for Bitmap {
    fn drop(&mut self) {
        unsafe {
            if !self.previous.is_null() {
                SelectObject(self.memory, self.previous);
            }
            if !self.bitmap.is_null() {
                DeleteObject(self.bitmap);
            }
            if !self.memory.is_null() {
                DeleteDC(self.memory);
            }
            if !self.screen.is_null() {
                ReleaseDC(null_mut(), self.screen);
            }
        }
    }
}

fn capture_pixels(g: &Geometry) -> Result<RgbImage, EIPError> {
    let (width, height) = (g.width as u32, g.height as u32);
    if width == 0 || height == 0 || u64::from(width) * u64::from(height) > 64_000_000 {
        return Err(error(
            ErrorType::QuotaExceeded,
            "display exceeds the 64 megapixel capture limit",
        ));
    }
    unsafe {
        let mut handles = Bitmap {
            screen: GetDC(null_mut()),
            memory: null_mut(),
            bitmap: null_mut(),
            previous: null_mut(),
        };
        if handles.screen.is_null() {
            return Err(unavailable());
        }
        handles.memory = CreateCompatibleDC(handles.screen);
        if handles.memory.is_null() {
            return Err(unavailable());
        }
        let mut info = BITMAPINFO::default();
        info.bmiHeader.biSize = size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = width as i32;
        info.bmiHeader.biHeight = -(height as i32);
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        let mut bits = null_mut();
        handles.bitmap = CreateDIBSection(
            handles.screen,
            &info,
            DIB_RGB_COLORS,
            &mut bits,
            null_mut(),
            0,
        );
        if handles.bitmap.is_null() || bits.is_null() {
            return Err(unavailable());
        }
        handles.previous = SelectObject(handles.memory, handles.bitmap);
        if handles.previous.is_null() {
            return Err(unavailable());
        }
        if BitBlt(
            handles.memory,
            0,
            0,
            width as i32,
            height as i32,
            handles.screen,
            g.x as i32,
            g.y as i32,
            SRCCOPY | CAPTUREBLT,
        ) == 0
            || GdiFlush() == 0
        {
            return Err(unavailable());
        }
        let pixels =
            std::slice::from_raw_parts(bits.cast::<u8>(), width as usize * height as usize * 4);
        let rgb = pixels
            .chunks_exact(4)
            .flat_map(|p| [p[2], p[1], p[0]])
            .collect();
        RgbImage::from_raw(width, height, rgb).ok_or_else(unavailable)
    }
}

impl Backend {
    pub(super) fn describe(&self) -> Result<ComputerDescribeResult, EIPError> {
        ready()?;
        let _dpi = Dpi::enter()?;
        Ok(ComputerDescribeResult {
            targets: monitors()?
                .into_iter()
                .map(|(g, _)| ComputerTarget {
                    name: g.target_id.clone(),
                    target_id: g.target_id,
                    width: g.width as u32,
                    height: g.height as u32,
                })
                .collect(),
            observe_ready: true,
            input_ready: true,
            scroll_units: vec![ComputerScrollUnit::Steps],
        })
    }
    pub(super) fn capture(
        &self,
        target: Option<&str>,
        max_dimension: u32,
    ) -> Result<Capture, EIPError> {
        ready()?;
        let _dpi = Dpi::enter()?;
        let geometry = select(target)?;
        let image = DynamicImage::ImageRgb8(capture_pixels(&geometry)?)
            .resize(max_dimension, max_dimension, FilterType::Triangle)
            .to_rgb8();
        ready()?;
        if select(Some(&geometry.target_id))? != geometry {
            return Err(error(
                ErrorType::Conflict,
                "display layout changed during capture",
            ));
        }
        let mut bytes = Vec::new();
        JpegEncoder::new_with_quality(&mut bytes, 80)
            .encode_image(&image)
            .map_err(|_| unavailable())?;
        Ok(Capture {
            geometry,
            width: image.width(),
            height: image.height(),
            bytes,
        })
    }
    pub(super) fn execute(
        &self,
        action: &Action,
        basis: Option<&Basis>,
        interrupted: &dyn Fn() -> bool,
    ) -> Result<Effect, EIPError> {
        if let Action::Scroll(p) = action
            && p.unit != Some(ComputerScrollUnit::Steps)
        {
            return Err(reason(
                ErrorType::Unsupported,
                "computer_scroll_steps_required",
                "Windows scroll requires unit=steps; pixel scrolling is not supported",
            ));
        }
        ready()?;
        let _dpi = Dpi::enter()?;
        if let Some(b) = basis
            && select(Some(&b.geometry.target_id))? != b.geometry
        {
            return Err(error(
                ErrorType::Conflict,
                "display layout changed; capture a fresh image",
            ));
        }
        let virtual_desktop = unsafe {
            (
                GetSystemMetrics(SM_XVIRTUALSCREEN),
                GetSystemMetrics(SM_YVIRTUALSCREEN),
                GetSystemMetrics(SM_CXVIRTUALSCREEN),
                GetSystemMetrics(SM_CYVIRTUALSCREEN),
            )
        };
        if virtual_desktop.2 <= 0 || virtual_desktop.3 <= 0 {
            return Err(unavailable());
        }
        let point = |p: &ComputerPoint| -> Result<(i32, i32), EIPError> {
            let (x, y) = basis
                .ok_or_else(|| error(ErrorType::InvalidParams, "observation required"))?
                .point(p)?;
            Ok((
                normalize(x, virtual_desktop.0, virtual_desktop.2),
                normalize(y, virtual_desktop.1, virtual_desktop.3),
            ))
        };
        // Resolve every fallible payload transformation before any native input.
        let (start, end) = match action {
            Action::Move(p) => (point(&p.point)?, None),
            Action::Click(p) => (point(&p.point)?, None),
            Action::Scroll(p) => (point(&p.point)?, None),
            Action::Drag(p) => (point(&p.start)?, Some(point(&p.end)?)),
            _ => ((0, 0), None),
        };
        let keys = if let Action::PressKeys(p) = action {
            p.keys
                .iter()
                .map(|name| {
                    scan_code(name)
                        .ok_or_else(|| error(ErrorType::InvalidParams, "unsupported physical key"))
                })
                .collect::<Result<Vec<_>, _>>()?
        } else {
            Vec::new()
        };
        let swapped = unsafe { GetSystemMetrics(SM_SWAPBUTTON) != 0 };
        let button = match action {
            Action::Click(p) => Some(mouse_button(p.button, swapped)),
            Action::Drag(p) => Some(mouse_button(p.button, swapped)),
            _ => None,
        };
        let mut held_vks = Vec::new();
        if !keys.is_empty() {
            // Scan codes are interpreted by the foreground thread's layout, which
            // can differ from this worker's layout after another app switches it.
            let thread = unsafe { GetWindowThreadProcessId(GetForegroundWindow(), null_mut()) };
            if thread == 0 {
                return Err(unavailable());
            }
            let layout = unsafe { GetKeyboardLayout(thread) };
            held_vks.extend(keys.iter().map(|(code, extended)| unsafe {
                MapVirtualKeyExW(
                    u32::from(*code) | if *extended { 0xe000 } else { 0 },
                    MAPVK_VSC_TO_VK_EX,
                    layout,
                )
            }));
        }
        if let Some((_, _, vk)) = button {
            held_vks.push(u32::from(vk));
        }
        if matches!(action, Action::TypeText(_)) {
            held_vks.extend([VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN].map(u32::from));
        }
        if held_vks
            .iter()
            .any(|vk| unsafe { GetAsyncKeyState(*vk as i32) < 0 })
        {
            return Err(reason(
                ErrorType::Conflict,
                "computer_input_held",
                "required input is already held; release it manually before a new action",
            ));
        }
        let mut input = Input::new(native_post);
        let check = || if interrupted() { Err(()) } else { Ok(()) };
        let outcome = (|| -> Result<(), ()> {
            check()?;
            match action {
                Action::Move(_) => input.post(motion(start))?,
                Action::Click(p) => {
                    input.post(motion(start))?;
                    let (down, up, _) = button.unwrap();
                    for _ in 0..p.count {
                        check()?;
                        input.press(mouse(down, 0), mouse(up, 0))?;
                        check()?;
                        input.release()?;
                    }
                }
                Action::Drag(p) => {
                    input.post(motion(start))?;
                    check()?;
                    let (down, up, _) = button.unwrap();
                    input.press(mouse(down, 0), mouse(up, 0))?;
                    let end = end.unwrap();
                    let steps = (p.duration_ms / 16).max(1);
                    for step in 1..=steps {
                        std::thread::sleep(Duration::from_millis(u64::from(p.duration_ms / steps)));
                        check()?;
                        let fraction = f64::from(step) / f64::from(steps);
                        input.post(motion((
                            (f64::from(start.0) + f64::from(end.0 - start.0) * fraction) as i32,
                            (f64::from(start.1) + f64::from(end.1 - start.1) * fraction) as i32,
                        )))?;
                    }
                    input.release()?;
                }
                Action::Scroll(p) => {
                    input.post(motion(start))?;
                    for (delta, flag) in [
                        (-p.delta_y, MOUSEEVENTF_WHEEL),
                        (p.delta_x, MOUSEEVENTF_HWHEEL),
                    ] {
                        if delta != 0 {
                            check()?;
                            input.post(mouse(flag, (delta * 120) as u32))?;
                        }
                    }
                }
                Action::PressKeys(_) => {
                    for (code, extended) in keys {
                        check()?;
                        let flags =
                            KEYEVENTF_SCANCODE | if extended { KEYEVENTF_EXTENDEDKEY } else { 0 };
                        input.press(key(code, flags), key(code, flags | KEYEVENTF_KEYUP))?;
                    }
                    while !input.held.is_empty() {
                        check()?;
                        input.release()?;
                    }
                }
                Action::TypeText(p) => {
                    for code in text_units(&p.text) {
                        check()?;
                        input.press(
                            key(code, KEYEVENTF_UNICODE),
                            key(code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                        )?;
                        input.release()?;
                    }
                }
            }
            Ok(())
        })();
        // No EIP Err after possible input: daemon interprets every Err as pre-dispatch.
        Ok(input.finish(outcome.is_ok()))
    }
}

// Windows text controls interpret CR and LF as separate line-break events.
// Submit a CRLF pair once; preserve standalone controls and UTF-16 surrogate pairs.
fn text_units(text: &str) -> impl Iterator<Item = u16> + '_ {
    let mut previous = None;
    text.encode_utf16().filter(move |code| {
        let keep = !(*code == 10 && previous == Some(13));
        previous = Some(*code);
        keep
    })
}

fn normalize(value: f64, origin: i32, size: i32) -> i32 {
    (((value - f64::from(origin)).floor() + 0.5) * 65536.0 / f64::from(size)).clamp(0.0, 65535.0)
        as i32
}
fn motion((x, y): (i32, i32)) -> INPUT {
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx: x,
                dy: y,
                dwFlags: MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                ..Default::default()
            },
        },
    }
}
fn mouse(flags: u32, data: u32) -> INPUT {
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dwFlags: flags,
                mouseData: data,
                ..Default::default()
            },
        },
    }
}
fn key(code: u16, flags: u32) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wScan: code,
                dwFlags: flags,
                ..Default::default()
            },
        },
    }
}
fn mouse_button(button: ComputerButton, swapped: bool) -> (u32, u32, u16) {
    match (button, swapped) {
        (ComputerButton::Middle, _) => (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, VK_MBUTTON),
        (ComputerButton::Left, false) | (ComputerButton::Right, true) => {
            (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, VK_LBUTTON)
        }
        _ => (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, VK_RBUTTON),
    }
}

fn native_post(event: &INPUT) -> bool {
    ready().is_ok() && unsafe { SendInput(1, event, size_of::<INPUT>() as i32) == 1 }
}

struct Input<F: FnMut(&INPUT) -> bool> {
    send: F,
    held: Vec<INPUT>,
    accepted: usize,
    failed: bool,
}
impl<F: FnMut(&INPUT) -> bool> Input<F> {
    fn new(send: F) -> Self {
        Self {
            send,
            held: Vec::new(),
            accepted: 0,
            failed: false,
        }
    }
    fn post(&mut self, event: INPUT) -> Result<(), ()> {
        if (self.send)(&event) {
            self.accepted += 1;
            Ok(())
        } else {
            self.failed = true;
            Err(())
        }
    }
    fn press(&mut self, down: INPUT, up: INPUT) -> Result<(), ()> {
        self.post(down)?;
        self.held.push(up);
        Ok(())
    }
    fn release(&mut self) -> Result<(), ()> {
        if let Some(up) = self.held.last().copied() {
            self.post(up)?;
            self.held.pop();
        }
        Ok(())
    }
    fn finish(&mut self, completed: bool) -> Effect {
        let effect = if self.failed {
            ComputerEffect::Unknown
        } else if completed {
            ComputerEffect::Executed
        } else if self.accepted == 0 {
            ComputerEffect::NotExecuted
        } else {
            ComputerEffect::Partial
        };
        let mut cleanup_complete = true;
        // Exactly one cleanup attempt per outstanding release; never resend a gesture.
        while let Some(up) = self.held.pop() {
            cleanup_complete &= (self.send)(&up);
        }
        Effect {
            effect,
            cleanup_complete,
        }
    }
}
impl<F: FnMut(&INPUT) -> bool> Drop for Input<F> {
    fn drop(&mut self) {
        while let Some(up) = self.held.pop() {
            (self.send)(&up);
        }
    }
}

fn scan_code(name: &str) -> Option<(u16, bool)> {
    for (names, start) in [
        ("1234567890-=", 0x02),
        ("qwertyuiop[]", 0x10),
        ("asdfghjkl;'", 0x1e),
        ("zxcvbnm,./", 0x2c),
    ] {
        if name.len() == 1
            && let Some(index) = names.find(name)
        {
            return Some((start + index as u16, false));
        }
    }
    if let Some(n) = name.strip_prefix('f').and_then(|n| n.parse::<u16>().ok())
        && (1..=12).contains(&n)
    {
        return Some((if n <= 10 { 0x3a + n } else { 0x57 + n - 11 }, false));
    }
    Some(match name {
        "escape" => (0x01, false),
        "backspace" => (0x0e, false),
        "tab" => (0x0f, false),
        "enter" => (0x1c, false),
        "control" => (0x1d, false),
        "shift" => (0x2a, false),
        "\\" => (0x2b, false),
        "alt" => (0x38, false),
        "space" => (0x39, false),
        "`" => (0x29, false),
        "meta" => (0x5b, true),
        "home" => (0x47, true),
        "up" => (0x48, true),
        "page_up" => (0x49, true),
        "left" => (0x4b, true),
        "right" => (0x4d, true),
        "end" => (0x4f, true),
        "down" => (0x50, true),
        "page_down" => (0x51, true),
        "delete" => (0x53, true),
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_active_unlocked_sessions_are_ready() {
        let mut info = WTSINFOEXW {
            Level: 1,
            Data: WTSINFOEX_LEVEL_W {
                WTSInfoExLevel1: WTSINFOEX_LEVEL1_W {
                    SessionState: WTSActive,
                    SessionFlags: WTS_SESSIONSTATE_UNLOCK as i32,
                    ..Default::default()
                },
            },
        };
        assert!(session_ready(&info));
        info.Data.WTSInfoExLevel1.SessionFlags = WTS_SESSIONSTATE_LOCK as i32;
        assert!(!session_ready(&info));
        info.Data.WTSInfoExLevel1.SessionFlags = WTS_SESSIONSTATE_UNKNOWN as i32;
        assert!(!session_ready(&info));
        info.Data.WTSInfoExLevel1.SessionFlags = WTS_SESSIONSTATE_UNLOCK as i32;
        info.Data.WTSInfoExLevel1.SessionState = WTSDisconnected;
        assert!(!session_ready(&info));
        info.Data.WTSInfoExLevel1.SessionState = WTSActive;
        info.Level = 0;
        assert!(!session_ready(&info));
    }
    #[test]
    fn text_line_breaks_do_not_duplicate_crlf() {
        assert_eq!(
            text_units("a\r\nb\nc\rd\t\u{1f600}").collect::<Vec<_>>(),
            "a\rb\nc\rd\t\u{1f600}".encode_utf16().collect::<Vec<_>>()
        );
    }
    #[test]
    fn physical_keys_and_negative_origin_pixel_centers() {
        assert_eq!(scan_code("a"), Some((0x1e, false)));
        assert_eq!(scan_code("meta"), Some((0x5b, true)));
        assert_eq!(scan_code("f12"), Some((0x58, false)));
        assert_eq!(scan_code("unknown"), None);
        assert_eq!(normalize(-1920.0, -1920, 3840), 8);
        assert_eq!(normalize(1919.0, -1920, 3840), 65527);
    }
    #[test]
    fn failed_down_does_not_release_an_unaccepted_input() {
        let mut calls = 0;
        let mut input = Input::new(|_| {
            calls += 1;
            false
        });
        assert!(input.press(key(1, 0), key(1, KEYEVENTF_KEYUP)).is_err());
        let effect = input.finish(false);
        assert_eq!(effect.effect, ComputerEffect::Unknown);
        assert!(effect.cleanup_complete);
        drop(input);
        assert_eq!(calls, 1);
    }
    #[test]
    fn interruption_releases_only_this_gestures_keys_in_reverse_order() {
        let mut events = Vec::new();
        let mut input = Input::new(|event| {
            events.push(unsafe { (event.Anonymous.ki.wScan, event.Anonymous.ki.dwFlags) });
            true
        });
        input.press(key(1, 0), key(1, KEYEVENTF_KEYUP)).unwrap();
        input.press(key(2, 0), key(2, KEYEVENTF_KEYUP)).unwrap();
        let effect = input.finish(false);
        assert_eq!(effect.effect, ComputerEffect::Partial);
        assert!(effect.cleanup_complete);
        drop(input);
        assert_eq!(
            events,
            vec![(1, 0), (2, 0), (2, KEYEVENTF_KEYUP), (1, KEYEVENTF_KEYUP)]
        );
    }
    #[test]
    fn failed_cleanup_is_not_rollback_or_success() {
        let mut calls = 0;
        let mut input = Input::new(|_| {
            calls += 1;
            calls == 1
        });
        input.press(key(1, 0), key(1, KEYEVENTF_KEYUP)).unwrap();
        assert!(input.release().is_err());
        let effect = input.finish(false);
        assert_eq!(effect.effect, ComputerEffect::Unknown);
        assert!(!effect.cleanup_complete);
        drop(input);
        assert_eq!(calls, 3);
    }
    #[test]
    fn interruption_before_dispatch_is_not_executed() {
        let mut input = Input::new(|_| panic!("no input"));
        let effect = input.finish(false);
        assert_eq!(effect.effect, ComputerEffect::NotExecuted);
        assert!(effect.cleanup_complete);
    }
}
