//! Eager text on the general pasteboard; Objective-C objects stay worker-local.
#![allow(unsafe_code, reason = "AppKit pasteboard type is an external static")]
use super::{clipboard::*, *};
use objc2::{rc::autoreleasepool, sel};
use objc2_app_kit::{NSPasteboard, NSPasteboardAccessBehavior, NSPasteboardTypeString};
use objc2_foundation::{NSObjectProtocol, NSString};

fn unavailable() -> EIPError {
    error(
        ErrorType::ProviderUnavailable,
        "macOS pasteboard is unavailable",
    )
}
fn read_denied() -> EIPError {
    error(
        ErrorType::Denied,
        "pasteboard text could not be read; check macOS clipboard access consent",
    )
}

pub(super) fn read(
    max: u32,
    interrupted: &dyn Fn() -> bool,
) -> Result<ComputerReadClipboardResult, EIPError> {
    autoreleasepool(|_| {
        check(interrupted)?;
        let board = NSPasteboard::generalPasteboard();
        // accessBehavior is available from macOS 15.4; keep older systems usable.
        if board.respondsToSelector(sel!(accessBehavior))
            && board.accessBehavior() == NSPasteboardAccessBehavior::AlwaysDeny
        {
            return Err(read_denied());
        }
        let revision = board.changeCount();
        let items = board.pasteboardItems().ok_or_else(unavailable)?;
        let mut result = empty(if items.is_empty() {
            ComputerClipboardStatus::NoContent
        } else {
            ComputerClipboardStatus::NoText
        });
        for index in 0..items.count() {
            let item = items.objectAtIndex(index);
            check(interrupted)?;
            if !item
                .types()
                .containsObject(unsafe { NSPasteboardTypeString })
            {
                continue;
            }
            let data = item
                .dataForType(unsafe { NSPasteboardTypeString })
                .ok_or_else(read_denied)?;
            if data.length() > MAX_NATIVE_BYTES {
                return Err(oversize());
            }
            let value = String::from_utf8(data.to_vec()).map_err(|_| invalid_text())?;
            result = text(value, max)?;
            break;
        }
        check(interrupted)?;
        if board.changeCount() != revision {
            return Err(error(ErrorType::Conflict, "pasteboard changed during read"));
        }
        Ok(result)
    })
}

pub(super) fn write(value: &str, interrupted: &dyn Fn() -> bool) -> Result<Effect, EIPError> {
    autoreleasepool(|_| {
        let value = NSString::from_str(value);
        let board = NSPasteboard::generalPasteboard();
        check(interrupted)?;
        // clearContents is the mutation boundary; a later publication failure is
        // partial, never a retry-safe pre-dispatch error.
        if board.clearContents() < 0 {
            return Err(unavailable());
        }
        if board.setString_forType(&value, unsafe { NSPasteboardTypeString }) {
            Ok(effect(ComputerEffect::Executed))
        } else {
            Ok(effect(ComputerEffect::Partial))
        }
    })
}
