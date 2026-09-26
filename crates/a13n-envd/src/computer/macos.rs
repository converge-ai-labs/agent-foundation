//! Native macOS capture and bounded, complete input gestures.
//! Each operation cleans up only the input it pressed. There is no task-level lock.
#![allow(
    unsafe_code,
    reason = "CoreGraphics Unicode input accepts a live UTF-16 slice"
)]

use super::*;
use image::{DynamicImage, codecs::jpeg::JpegEncoder, imageops::FilterType};
use objc2_core_foundation::CGPoint;
use objc2_core_graphics::{
    CGEvent, CGEventField, CGEventFlags, CGEventTapLocation, CGEventType, CGMouseButton,
    CGPreflightPostEventAccess, CGPreflightScreenCaptureAccess, CGRequestPostEventAccess,
    CGRequestScreenCaptureAccess, CGScrollEventUnit,
};
use xcap::Monitor;

pub(super) fn permissions() -> startup::Permissions {
    startup::Permissions {
        capture: CGPreflightScreenCaptureAccess(),
        input: CGPreflightPostEventAccess(),
    }
}

pub(super) fn request_permissions(current: startup::Permissions) {
    // Native prompts may wait on the user. Keep them off the async runtime so
    // permission polling, cancellation and the startup deadline remain live.
    // Runtime shutdown is bounded even if a native prompt has not returned.
    if !current.capture {
        tokio::task::spawn_blocking(|| CGRequestScreenCaptureAccess());
    }
    if !current.input {
        tokio::task::spawn_blocking(|| CGRequestPostEventAccess());
    }
}

fn unavailable() -> EIPError {
    error(
        ErrorType::ProviderUnavailable,
        "macOS desktop is unavailable",
    )
}
fn denied() -> EIPError {
    error(
        ErrorType::Denied,
        "grant Screen Recording and Accessibility permissions to envd in macOS System Settings",
    )
}

fn geometry(monitor: &Monitor) -> Result<Geometry, EIPError> {
    Ok(Geometry {
        target_id: format!("display-{}", monitor.id().map_err(|_| unavailable())?),
        x: f64::from(monitor.x().map_err(|_| unavailable())?),
        y: f64::from(monitor.y().map_err(|_| unavailable())?),
        width: f64::from(monitor.width().map_err(|_| unavailable())?),
        height: f64::from(monitor.height().map_err(|_| unavailable())?),
    })
}

fn select(target: Option<&str>) -> Result<Monitor, EIPError> {
    let monitors = Monitor::all().map_err(|_| unavailable())?;
    for monitor in monitors {
        if match target {
            Some(target) => geometry(&monitor)?.target_id == target,
            None => monitor.is_primary().map_err(|_| unavailable())?,
        } {
            return Ok(monitor);
        }
    }
    Err(error(
        ErrorType::NotFoundOrDenied,
        "display target is unavailable",
    ))
}

pub(super) fn describe() -> Result<ComputerDescribeResult, EIPError> {
    let targets = Monitor::all()
        .map_err(|_| unavailable())?
        .into_iter()
        .map(|m| {
            let g = geometry(&m)?;
            Ok(ComputerTarget {
                target_id: g.target_id,
                name: m.name().map_err(|_| unavailable())?,
                width: m.width().map_err(|_| unavailable())?,
                height: m.height().map_err(|_| unavailable())?,
            })
        })
        .collect::<Result<_, EIPError>>()?;
    Ok(ComputerDescribeResult {
        targets,
        observe_ready: CGPreflightScreenCaptureAccess(),
        input_ready: CGPreflightPostEventAccess(),
    })
}

pub(super) fn capture(target: Option<&str>, max_dimension: u32) -> Result<Capture, EIPError> {
    if !CGPreflightScreenCaptureAccess() {
        return Err(denied());
    }
    let monitor = select(target)?;
    let before = geometry(&monitor)?;
    let image = monitor.capture_image().map_err(|_| unavailable())?;
    let image = DynamicImage::ImageRgba8(image)
        .resize(max_dimension, max_dimension, FilterType::Triangle)
        .to_rgb8();
    let mut bytes = Vec::new();
    JpegEncoder::new_with_quality(&mut bytes, 80)
        .encode_image(&image)
        .map_err(|_| unavailable())?;
    if !CGPreflightScreenCaptureAccess() {
        return Err(denied());
    }
    if geometry(&select(Some(&before.target_id))?)? != before {
        return Err(error(
            ErrorType::Conflict,
            "display layout changed during capture",
        ));
    }
    Ok(Capture {
        geometry: before,
        width: image.width(),
        height: image.height(),
        bytes,
    })
}

struct Input {
    keys: Vec<u16>,
    mouse: Option<(ComputerButton, CGPoint)>,
    flags: CGEventFlags,
    posted: usize,
}

impl Input {
    fn new() -> Self {
        Self {
            keys: Vec::new(),
            mouse: None,
            flags: CGEventFlags::empty(),
            posted: 0,
        }
    }
    fn post(&mut self, event: &CGEvent) -> Result<(), ()> {
        if !CGPreflightPostEventAccess() {
            return Err(());
        }
        CGEvent::post(CGEventTapLocation::HIDEventTap, Some(event));
        self.posted += 1;
        Ok(())
    }
    fn mouse_event(
        &mut self,
        kind: CGEventType,
        button: ComputerButton,
        point: CGPoint,
        count: u32,
    ) -> Result<(), ()> {
        let event = CGEvent::new_mouse_event(None, kind, point, native_button(button)).ok_or(())?;
        CGEvent::set_integer_value_field(
            Some(&event),
            CGEventField::MouseEventClickState,
            i64::from(count),
        );
        self.post(&event)
    }
    fn mouse_down(&mut self, button: ComputerButton, point: CGPoint, count: u32) -> Result<(), ()> {
        self.mouse_event(mouse_kind(button, true, false), button, point, count)?;
        self.mouse = Some((button, point));
        Ok(())
    }
    fn mouse_up(&mut self, button: ComputerButton, point: CGPoint, count: u32) -> Result<(), ()> {
        self.mouse_event(mouse_kind(button, false, false), button, point, count)?;
        self.mouse = None;
        Ok(())
    }
    fn key(&mut self, code: u16, down: bool) -> Result<(), ()> {
        let flag = match code {
            55 => CGEventFlags::MaskCommand,
            56 => CGEventFlags::MaskShift,
            58 => CGEventFlags::MaskAlternate,
            59 => CGEventFlags::MaskControl,
            _ => CGEventFlags::empty(),
        };
        if down {
            self.flags.insert(flag);
        } else {
            self.flags.remove(flag);
        }
        let event = CGEvent::new_keyboard_event(None, code, down).ok_or(())?;
        CGEvent::set_flags(Some(&event), self.flags);
        self.post(&event)?;
        if down {
            self.keys.push(code);
        } else if let Some(i) = self.keys.iter().rposition(|v| *v == code) {
            self.keys.remove(i);
        }
        Ok(())
    }
    fn unicode(&mut self, text: &[u16]) -> Result<(), ()> {
        for down in [true, false] {
            let event = CGEvent::new_keyboard_event(None, 0, down).ok_or(())?;
            // SAFETY: the immutable UTF-16 slice remains live for the synchronous native call.
            unsafe {
                CGEvent::keyboard_set_unicode_string(
                    Some(&event),
                    text.len() as core::ffi::c_ulong,
                    text.as_ptr(),
                );
            }
            self.post(&event)?;
            if down {
                self.keys.push(0);
            } else {
                self.keys.pop();
            }
        }
        Ok(())
    }
    fn cleanup(&mut self) -> bool {
        let keys = self.keys.clone();
        let mut complete = true;
        for key in keys.into_iter().rev() {
            complete &= self.key(key, false).is_ok();
        }
        if let Some((button, point)) = self.mouse {
            complete &= self.mouse_up(button, point, 1).is_ok();
        }
        complete
    }
}

impl Drop for Input {
    fn drop(&mut self) {
        if !self.keys.is_empty() || self.mouse.is_some() {
            let _ = self.cleanup();
        }
    }
}

fn native_button(button: ComputerButton) -> CGMouseButton {
    match button {
        ComputerButton::Left => CGMouseButton::Left,
        ComputerButton::Right => CGMouseButton::Right,
        ComputerButton::Middle => CGMouseButton::Center,
    }
}
fn mouse_kind(button: ComputerButton, down: bool, drag: bool) -> CGEventType {
    match (button, down, drag) {
        (ComputerButton::Left, _, true) => CGEventType::LeftMouseDragged,
        (ComputerButton::Right, _, true) => CGEventType::RightMouseDragged,
        (ComputerButton::Middle, _, true) => CGEventType::OtherMouseDragged,
        (ComputerButton::Left, true, _) => CGEventType::LeftMouseDown,
        (ComputerButton::Left, false, _) => CGEventType::LeftMouseUp,
        (ComputerButton::Right, true, _) => CGEventType::RightMouseDown,
        (ComputerButton::Right, false, _) => CGEventType::RightMouseUp,
        (ComputerButton::Middle, true, _) => CGEventType::OtherMouseDown,
        (ComputerButton::Middle, false, _) => CGEventType::OtherMouseUp,
    }
}

pub(super) fn execute(
    action: &Action,
    basis: Option<&Basis>,
    interrupted: &dyn Fn() -> bool,
) -> Result<Effect, EIPError> {
    if !CGPreflightPostEventAccess() {
        return Err(denied());
    }
    if let Some(basis) = basis
        && geometry(&select(Some(&basis.geometry.target_id))?)? != basis.geometry
    {
        return Err(error(
            ErrorType::Conflict,
            "display layout changed; capture a fresh image",
        ));
    }
    let point = |point: &ComputerPoint| -> Result<CGPoint, ()> {
        let (x, y) = basis.ok_or(())?.point(point).map_err(|_| ())?;
        Ok(CGPoint { x, y })
    };
    let check = || if interrupted() { Err(()) } else { Ok(()) };
    let mut input = Input::new();
    let result = (|| -> Result<(), ()> {
        check()?;
        match action {
            Action::Click(p) => {
                let target = point(&p.point)?;
                for count in 1..=p.count {
                    check()?;
                    input.mouse_down(p.button, target, count)?;
                    check()?;
                    input.mouse_up(p.button, target, count)?;
                }
            }
            Action::Move(p) => {
                input.mouse_event(
                    CGEventType::MouseMoved,
                    ComputerButton::Left,
                    point(&p.point)?,
                    1,
                )?;
            }
            Action::Drag(p) => {
                let start = point(&p.start)?;
                let end = point(&p.end)?;
                input.mouse_event(CGEventType::MouseMoved, p.button, start, 1)?;
                input.mouse_down(p.button, start, 1)?;
                let steps = (p.duration_ms / 16).max(1);
                for step in 1..=steps {
                    std::thread::sleep(Duration::from_millis(u64::from(p.duration_ms / steps)));
                    check()?;
                    let fraction = f64::from(step) / f64::from(steps);
                    let current = CGPoint {
                        x: start.x + (end.x - start.x) * fraction,
                        y: start.y + (end.y - start.y) * fraction,
                    };
                    input.mouse_event(mouse_kind(p.button, true, true), p.button, current, 1)?;
                    input.mouse = Some((p.button, current));
                }
                input.mouse_up(p.button, end, 1)?;
            }
            Action::Scroll(p) => {
                let event = CGEvent::new_scroll_wheel_event2(
                    None,
                    CGScrollEventUnit::Pixel,
                    2,
                    -p.delta_y,
                    -p.delta_x,
                    0,
                )
                .ok_or(())?;
                CGEvent::set_location(Some(&event), point(&p.point)?);
                input.post(&event)?;
            }
            Action::TypeText(p) => {
                // One scalar at a time avoids splitting surrogate pairs. Control characters
                // are physical keys so applications interpret tabs/newlines correctly.
                for scalar in p.text.chars() {
                    check()?;
                    match scalar {
                        '\n' | '\r' => {
                            input.key(36, true)?;
                            input.key(36, false)?;
                        }
                        '\t' => {
                            input.key(48, true)?;
                            input.key(48, false)?;
                        }
                        _ => {
                            let mut buffer = [0; 2];
                            input.unicode(scalar.encode_utf16(&mut buffer))?;
                        }
                    }
                }
            }
            Action::PressKeys(p) => {
                // Press modifiers first regardless of caller ordering.
                let mut codes: Vec<u16> = p
                    .keys
                    .iter()
                    .map(|k| key_code(k).ok_or(()))
                    .collect::<Result<_, _>>()?;
                codes.sort_by_key(|k| !matches!(k, 55 | 56 | 58 | 59));
                for code in &codes {
                    check()?;
                    input.key(*code, true)?;
                }
                for code in codes.into_iter().rev() {
                    check()?;
                    input.key(code, false)?;
                }
            }
        }
        Ok(())
    })();
    let posted = input.posted;
    let cleanup_complete = input.cleanup();
    Ok(Effect {
        effect: if result.is_ok() {
            ComputerEffect::Executed
        } else if posted == 0 {
            ComputerEffect::NotExecuted
        } else {
            ComputerEffect::Partial
        },
        cleanup_complete,
    })
}
