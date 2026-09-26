//! X11-only desktop access. No XWayland fallback, clipboard or keyboard-map mutation.
use super::*;
use image::{DynamicImage, RgbImage, codecs::jpeg::JpegEncoder, imageops::FilterType};
use std::sync::OnceLock;
use x11rb::{
    connection::Connection,
    protocol::{
        randr::ConnectionExt as _,
        xkb::{self, ConnectionExt as _},
        xproto::{self, ConnectionExt as _},
        xtest::ConnectionExt as _,
    },
    rust_connection::RustConnection,
};

#[derive(Default)]
pub(super) struct Backend {
    // Never reconnect within a Session: old geometry must not authorize a new X server.
    desktop: OnceLock<Result<Desktop, EIPError>>,
}

struct Desktop {
    connection: RustConnection,
    screen: usize,
}

fn unavailable() -> EIPError {
    error(
        ErrorType::ProviderUnavailable,
        "X11 desktop unavailable; check DISPLAY, XAUTHORITY and the X server",
    )
}
fn unsupported(message: &str) -> EIPError {
    error(ErrorType::Unsupported, message)
}

impl Desktop {
    fn connect() -> Result<Self, EIPError> {
        if std::env::var_os("WAYLAND_DISPLAY").is_some()
            || std::env::var("XDG_SESSION_TYPE").is_ok_and(|v| v.eq_ignore_ascii_case("wayland"))
        {
            return Err(unsupported(
                "Wayland and XWayland computer use are not supported",
            ));
        }
        if std::env::var("DISPLAY").map_or(true, |v| v.is_empty()) {
            return Err(unavailable());
        }
        let (connection, screen) = x11rb::connect(None).map_err(|_| unavailable())?;
        if connection
            .query_extension(b"XWAYLAND")
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?
            .present
        {
            return Err(unsupported("XWayland computer use is not supported"));
        }
        connection
            .xtest_get_version(2, 2)
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unsupported("X11 computer use requires XTEST"))?;
        let version = connection
            .randr_query_version(1, 5)
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unsupported("X11 computer use requires RandR 1.5"))?;
        if (version.major_version, version.minor_version) < (1, 5) {
            return Err(unsupported("X11 computer use requires RandR 1.5"));
        }
        if !connection
            .xkb_use_extension(1, 0)
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?
            .supported
        {
            return Err(unsupported("X11 computer use requires XKB"));
        }
        Ok(Self { connection, screen })
    }
    fn root(&self) -> u32 {
        self.connection.setup().roots[self.screen].root
    }

    fn targets(&self) -> Result<Vec<(Geometry, String, bool)>, EIPError> {
        let reply = self
            .connection
            .randr_get_monitors(self.root(), true)
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?;
        let mut targets = Vec::new();
        for monitor in reply.monitors {
            if monitor.width == 0 || monitor.height == 0 {
                continue;
            }
            let name = self
                .connection
                .get_atom_name(monitor.name)
                .map_err(|_| unavailable())?
                .reply()
                .map_err(|_| unavailable())?;
            targets.push((
                Geometry {
                    target_id: format!("display-{}-{}", self.screen, monitor.name),
                    x: f64::from(monitor.x),
                    y: f64::from(monitor.y),
                    width: f64::from(monitor.width),
                    height: f64::from(monitor.height),
                },
                String::from_utf8_lossy(&name.name).into_owned(),
                monitor.primary,
            ));
        }
        if targets.is_empty() {
            return Err(unavailable());
        }
        Ok(targets)
    }
    fn select(&self, target: Option<&str>) -> Result<Geometry, EIPError> {
        let targets = self.targets()?;
        let selected = if let Some(id) = target {
            targets.iter().find(|(g, _, _)| g.target_id == id)
        } else {
            targets
                .iter()
                .find(|(_, _, primary)| *primary)
                .or_else(|| targets.first())
        };
        selected.map(|(g, _, _)| g.clone()).ok_or_else(|| {
            error(
                ErrorType::Conflict,
                "display target unavailable; capture a fresh image",
            )
        })
    }
    fn keys(&self, names: &[String]) -> Result<Vec<u8>, EIPError> {
        let reply = self
            .connection
            .xkb_get_names(xkb::ID::USE_CORE_KBD.into(), xkb::NameDetail::KEY_NAMES)
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?;
        let keys = reply.value_list.key_names.ok_or_else(unavailable)?;
        let mut names = names.to_vec();
        names.sort_by_key(|name| !matches!(name.as_str(), "meta" | "control" | "alt" | "shift"));
        let mut codes = Vec::new();
        for name in names {
            let physical =
                key_name(&name).ok_or_else(|| unsupported("unsupported physical key"))?;
            let index = keys
                .iter()
                .position(|k| k.name == physical)
                .ok_or_else(|| unsupported("physical key is absent from the XKB keymap"))?;
            let code =
                u8::try_from(usize::from(reply.first_key) + index).map_err(|_| unavailable())?;
            if !codes.contains(&code) {
                codes.push(code);
            }
        }
        let held = self
            .connection
            .query_keymap()
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?;
        if codes
            .iter()
            .any(|code| held.keys[usize::from(*code / 8)] & (1 << (*code % 8)) != 0)
        {
            return Err(error(
                ErrorType::Conflict,
                "a requested key is already held",
            ));
        }
        Ok(codes)
    }
}

impl Backend {
    fn desktop(&self) -> Result<&Desktop, EIPError> {
        self.desktop
            .get_or_init(Desktop::connect)
            .as_ref()
            .map_err(Clone::clone)
    }
    pub(super) fn describe(&self) -> Result<ComputerDescribeResult, EIPError> {
        let desktop = self.desktop()?;
        Ok(ComputerDescribeResult {
            targets: desktop
                .targets()?
                .into_iter()
                .map(|(g, name, _)| ComputerTarget {
                    target_id: g.target_id,
                    name,
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
        let desktop = self.desktop()?;
        let geometry = desktop.select(target)?;
        let width = geometry.width as u16;
        let height = geometry.height as u16;
        if u64::from(width) * u64::from(height) > 64 * 1024 * 1024 {
            return Err(error(
                ErrorType::QuotaExceeded,
                "X11 capture exceeds 64 megapixels",
            ));
        }
        let setup = desktop.connection.setup();
        let screen = &setup.roots[desktop.screen];
        let visual = screen
            .allowed_depths
            .iter()
            .flat_map(|d| &d.visuals)
            .find(|v| v.visual_id == screen.root_visual)
            .ok_or_else(unavailable)?;
        if visual.class != xproto::VisualClass::TRUE_COLOR {
            return Err(unsupported("X11 capture requires a TrueColor root visual"));
        }
        let format = setup
            .pixmap_formats
            .iter()
            .find(|f| f.depth == screen.root_depth)
            .ok_or_else(unavailable)?;
        if !matches!(format.bits_per_pixel, 16 | 24 | 32) {
            return Err(unsupported("unsupported X11 pixel format"));
        }
        let reply = desktop
            .connection
            .get_image(
                xproto::ImageFormat::Z_PIXMAP,
                desktop.root(),
                geometry.x as i16,
                geometry.y as i16,
                width,
                height,
                u32::MAX,
            )
            .map_err(|_| unavailable())?
            .reply()
            .map_err(|_| unavailable())?;
        let rgb = decode_image(
            &reply.data,
            u32::from(width),
            u32::from(height),
            format.bits_per_pixel,
            format.scanline_pad,
            setup.image_byte_order == xproto::ImageOrder::LSB_FIRST,
            [visual.red_mask, visual.green_mask, visual.blue_mask],
        )?;
        if desktop.select(Some(&geometry.target_id))? != geometry {
            return Err(error(
                ErrorType::Conflict,
                "display layout changed during capture",
            ));
        }
        let image = DynamicImage::ImageRgb8(rgb)
            .resize(max_dimension, max_dimension, FilterType::Triangle)
            .to_rgb8();
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
        // Reject unsupported semantics before connecting or moving the pointer.
        if matches!(action, Action::TypeText(_)) {
            return Err(unsupported(
                "X11 literal text input is not supported; no clipboard or keyboard-map fallback",
            ));
        }
        if let Action::Scroll(p) = action
            && p.unit != Some(ComputerScrollUnit::Steps)
        {
            return Err(unsupported(
                "X11 scroll requires unit=steps; pixel scrolling is not supported",
            ));
        }
        let desktop = self.desktop()?;
        if let Some(basis) = basis
            && desktop.select(Some(&basis.geometry.target_id))? != basis.geometry
        {
            return Err(error(
                ErrorType::Conflict,
                "display layout changed; capture a fresh image",
            ));
        }
        let codes = if let Action::PressKeys(p) = action {
            desktop.keys(&p.keys)?
        } else {
            Vec::new()
        };
        let required_buttons = match action {
            Action::Click(p) => vec![button(p.button)],
            Action::Drag(p) => vec![button(p.button)],
            Action::Scroll(p) => [(p.delta_y, 4, 5), (p.delta_x, 6, 7)]
                .into_iter()
                .filter(|(delta, _, _)| *delta != 0)
                .map(|(delta, negative, positive)| if delta < 0 { negative } else { positive })
                .collect(),
            _ => Vec::new(),
        };
        let mut buttons = [0u8; 8];
        if !required_buttons.is_empty() {
            let mapping = desktop
                .connection
                .get_pointer_mapping()
                .map_err(|_| unavailable())?
                .reply()
                .map_err(|_| unavailable())?
                .map;
            for logical in required_buttons {
                buttons[usize::from(logical)] = physical_button(&mapping, logical)?;
            }
        }
        let point = |p: &ComputerPoint| -> Result<(i16, i16), ()> {
            let (x, y) = basis.ok_or(())?.point(p).map_err(|_| ())?;
            if x < 0.0 || y < 0.0 || x > f64::from(i16::MAX) || y > f64::from(i16::MAX) {
                return Err(());
            }
            Ok((x as i16, y as i16))
        };
        let mut input = Input {
            desktop,
            held: Vec::new(),
            attempted: 0,
            failed: false,
        };
        let check = || if interrupted() { Err(()) } else { Ok(()) };
        let result = (|| -> Result<(), ()> {
            check()?;
            match action {
                Action::Move(p) => input.motion(point(&p.point)?)?,
                Action::Click(p) => {
                    input.motion(point(&p.point)?)?;
                    for _ in 0..p.count {
                        check()?;
                        input.press(
                            xproto::BUTTON_PRESS_EVENT,
                            buttons[usize::from(button(p.button))],
                        )?;
                        check()?;
                        input.release(
                            xproto::BUTTON_RELEASE_EVENT,
                            buttons[usize::from(button(p.button))],
                        )?;
                    }
                }
                Action::Drag(p) => {
                    let start = point(&p.start)?;
                    let end = point(&p.end)?;
                    input.motion(start)?;
                    check()?;
                    input.press(
                        xproto::BUTTON_PRESS_EVENT,
                        buttons[usize::from(button(p.button))],
                    )?;
                    let steps = (p.duration_ms / 16).max(1);
                    for step in 1..=steps {
                        std::thread::sleep(Duration::from_millis(u64::from(p.duration_ms / steps)));
                        check()?;
                        let fraction = f64::from(step) / f64::from(steps);
                        input.motion((
                            (f64::from(start.0) + f64::from(end.0 - start.0) * fraction) as i16,
                            (f64::from(start.1) + f64::from(end.1 - start.1) * fraction) as i16,
                        ))?;
                    }
                    input.release(
                        xproto::BUTTON_RELEASE_EVENT,
                        buttons[usize::from(button(p.button))],
                    )?;
                }
                Action::Scroll(p) => {
                    input.motion(point(&p.point)?)?;
                    for (delta, negative, positive) in [(p.delta_y, 4, 5), (p.delta_x, 6, 7)] {
                        for _ in 0..delta.unsigned_abs() {
                            check()?;
                            let code = buttons[if delta < 0 { negative } else { positive }];
                            input.press(xproto::BUTTON_PRESS_EVENT, code)?;
                            check()?;
                            input.release(xproto::BUTTON_RELEASE_EVENT, code)?;
                        }
                    }
                }
                Action::PressKeys(_) => {
                    for code in &codes {
                        check()?;
                        input.press(xproto::KEY_PRESS_EVENT, *code)?;
                    }
                    for code in codes.iter().rev() {
                        check()?;
                        input.release(xproto::KEY_RELEASE_EVENT, *code)?;
                    }
                }
                Action::TypeText(_) => unreachable!(),
            }
            Ok(())
        })();
        let attempted = input.attempted;
        let failed = input.failed;
        let cleanup_complete = input.cleanup();
        Ok(Effect {
            effect: if failed {
                ComputerEffect::Unknown
            } else if result.is_ok() {
                ComputerEffect::Executed
            } else if attempted == 0 {
                ComputerEffect::NotExecuted
            } else {
                ComputerEffect::Partial
            },
            cleanup_complete,
        })
    }
}

struct Input<'a> {
    desktop: &'a Desktop,
    held: Vec<(u8, u8)>,
    attempted: usize,
    failed: bool,
}
impl Input<'_> {
    fn post(&mut self, kind: u8, code: u8, x: i16, y: i16) -> Result<(), ()> {
        self.attempted += 1;
        let result = self
            .desktop
            .connection
            .xtest_fake_input(
                kind,
                code,
                x11rb::CURRENT_TIME,
                self.desktop.root(),
                x,
                y,
                0,
            )
            .map_err(|_| ())
            .and_then(|cookie| cookie.check().map_err(|_| ()));
        self.failed |= result.is_err();
        result
    }
    fn motion(&mut self, (x, y): (i16, i16)) -> Result<(), ()> {
        self.post(xproto::MOTION_NOTIFY_EVENT, 0, x, y)
    }
    fn press(&mut self, kind: u8, code: u8) -> Result<(), ()> {
        // A lost reply may still have pressed the input; include it in cleanup.
        self.held.push((kind + 1, code));
        self.post(kind, code, 0, 0)
    }
    fn release(&mut self, kind: u8, code: u8) -> Result<(), ()> {
        self.post(kind, code, 0, 0)?;
        if let Some(index) = self.held.iter().rposition(|v| *v == (kind, code)) {
            self.held.remove(index);
        }
        Ok(())
    }
    fn cleanup(&mut self) -> bool {
        let mut complete = true;
        for (kind, code) in self.held.clone().into_iter().rev() {
            complete &= self.release(kind, code).is_ok();
        }
        complete
    }
}
impl Drop for Input<'_> {
    fn drop(&mut self) {
        if !self.held.is_empty() {
            let _ = self.cleanup();
        }
    }
}
fn physical_button(mapping: &[u8], logical: u8) -> Result<u8, EIPError> {
    mapping
        .iter()
        .position(|value| *value == logical)
        .and_then(|index| u8::try_from(index + 1).ok())
        .ok_or_else(|| unsupported("logical mouse button is absent from the X11 pointer mapping"))
}

fn button(button: ComputerButton) -> u8 {
    match button {
        ComputerButton::Left => 1,
        ComputerButton::Middle => 2,
        ComputerButton::Right => 3,
    }
}

fn key_name(key: &str) -> Option<[u8; 4]> {
    for (row, names) in [
        ("AE", "1234567890-="),
        ("AD", "qwertyuiop[]"),
        ("AC", "asdfghjkl;'"),
        ("AB", "zxcvbnm,./"),
    ] {
        if let Some(index) = names
            .chars()
            .position(|c| key.len() == 1 && key.starts_with(c))
        {
            return format!("{row}{:02}", index + 1).as_bytes().try_into().ok();
        }
    }
    if let Some(number) = key.strip_prefix('f').and_then(|v| v.parse::<u8>().ok())
        && (1..=12).contains(&number)
    {
        return format!("FK{number:02}").as_bytes().try_into().ok();
    }
    Some(*match key {
        "`" => b"TLDE",
        "\\" => b"BKSL",
        "meta" => b"LWIN",
        "alt" => b"LALT",
        "control" => b"LCTL",
        "shift" => b"LFSH",
        "enter" => b"RTRN",
        "tab" => b"TAB\0",
        "escape" => b"ESC\0",
        "space" => b"SPCE",
        "backspace" => b"BKSP",
        "delete" => b"DELE",
        "left" => b"LEFT",
        "right" => b"RGHT",
        "up" => b"UP\0\0",
        "down" => b"DOWN",
        "home" => b"HOME",
        "end" => b"END\0",
        "page_up" => b"PGUP",
        "page_down" => b"PGDN",
        _ => return None,
    })
}

fn decode_image(
    data: &[u8],
    width: u32,
    height: u32,
    bits: u8,
    pad: u8,
    little_endian: bool,
    masks: [u32; 3],
) -> Result<RgbImage, EIPError> {
    if !matches!(bits, 16 | 24 | 32) || !matches!(pad, 8 | 16 | 32) || masks.contains(&0) {
        return Err(unsupported("unsupported X11 pixel format"));
    }
    let stride =
        (width as usize * usize::from(bits)).div_ceil(usize::from(pad)) * usize::from(pad / 8);
    if data.len() != stride * height as usize {
        return Err(unavailable());
    }
    let mut image = RgbImage::new(width, height);
    for (x, y, pixel) in image.enumerate_pixels_mut() {
        let offset = y as usize * stride + x as usize * usize::from(bits / 8);
        let bytes = &data[offset..offset + usize::from(bits / 8)];
        let value = if little_endian {
            bytes
                .iter()
                .enumerate()
                .fold(0u32, |v, (i, b)| v | (u32::from(*b) << (i * 8)))
        } else {
            bytes.iter().fold(0u32, |v, b| (v << 8) | u32::from(*b))
        };
        for (channel, mask) in pixel.0.iter_mut().zip(masks) {
            let shift = mask.trailing_zeros();
            *channel = (u64::from((value & mask) >> shift) * 255 / u64::from(mask >> shift)) as u8;
        }
    }
    Ok(image)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pointer_mapping_preserves_logical_buttons_and_wheel_direction() {
        let mapping = [3, 2, 1, 5, 4, 7, 6];
        assert_eq!(physical_button(&mapping, 1).unwrap(), 3);
        assert_eq!(physical_button(&mapping, 5).unwrap(), 4);
        assert_eq!(physical_button(&mapping, 7).unwrap(), 6);
        assert!(physical_button(&[0, 2, 3], 1).is_err());
    }

    #[test]
    fn physical_keys_use_xkb_names_not_layout_symbols() {
        assert_eq!(key_name("a"), Some(*b"AC01"));
        assert_eq!(key_name("q"), Some(*b"AD01"));
        assert_eq!(key_name("f12"), Some(*b"FK12"));
        assert_eq!(key_name("up"), Some(*b"UP\0\0"));
        assert_eq!(key_name("unknown"), None);
    }
    #[test]
    fn decodes_byte_order_padding_and_truecolor_masks() {
        let masks = [0xff0000, 0xff00, 0xff];
        assert_eq!(
            decode_image(&[3, 2, 1, 0], 1, 1, 32, 32, true, masks)
                .unwrap()
                .as_raw(),
            &[1, 2, 3]
        );
        assert_eq!(
            decode_image(&[1, 2, 3, 0], 1, 1, 24, 32, false, masks)
                .unwrap()
                .as_raw(),
            &[1, 2, 3]
        );
        assert_eq!(
            decode_image(&[0, 248], 1, 1, 16, 16, true, [0xf800, 0x7e0, 0x1f])
                .unwrap()
                .as_raw(),
            &[255, 0, 0]
        );
        assert!(decode_image(&[], 1, 1, 32, 32, true, masks).is_err());
    }
}
