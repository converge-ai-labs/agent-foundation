//! Eager text on the general pasteboard; Objective-C objects stay worker-local.
#![allow(unsafe_code, reason = "AppKit pasteboard type is an external static")]
use super::{clipboard::*, *};
use objc2::{exception, rc::autoreleasepool, sel};
use objc2_app_kit::{NSPasteboard, NSPasteboardAccessBehavior, NSPasteboardTypeString};
use objc2_foundation::{NSObjectProtocol, NSString};
use std::panic::AssertUnwindSafe;

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
    native(|| {
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
    })?
}

// Keep foreign exceptions inside the native adapter. The closures retain no
// reusable Rust state whose invariants could be broken by native unwinding.
fn native<T>(call: impl FnOnce() -> T) -> Result<T, EIPError> {
    exception::catch(AssertUnwindSafe(call)).map_err(|_| unavailable())
}

fn replace_contents(clear: impl FnOnce() -> isize, publish: impl FnOnce() -> bool) -> Effect {
    // An exception from clear cannot prove whether mutation happened. Once clear
    // succeeds, any publication failure is partial, never a pre-dispatch error.
    match native(clear) {
        Ok(revision) if revision >= 0 => match native(publish) {
            Ok(true) => effect(ComputerEffect::Executed),
            _ => effect(ComputerEffect::Partial),
        },
        _ => effect(ComputerEffect::Unknown),
    }
}

pub(super) fn write(value: &str, interrupted: &dyn Fn() -> bool) -> Result<Effect, EIPError> {
    autoreleasepool(|_| {
        let (value, board) =
            native(|| (NSString::from_str(value), NSPasteboard::generalPasteboard()))?;
        check(interrupted)?;
        Ok(replace_contents(
            || board.clearContents(),
            || board.setString_forType(&value, unsafe { NSPasteboardTypeString }),
        ))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use objc2::{class, msg_send, rc::Retained};

    fn raise() -> ! {
        // Throw a real Objective-C exception, not a Rust panic.
        let exception: Retained<exception::Exception> = unsafe {
            msg_send![class!(NSException),
                exceptionWithName: &*NSString::from_str("NSPasteboardCommunicationException"),
                reason: &*NSString::from_str("injected publication failure"),
                userInfo: std::ptr::null::<objc2::runtime::AnyObject>()]
        };
        exception::throw(exception)
    }

    #[test]
    fn clipboard_native_exception_is_controlled() {
        assert_eq!(
            native(raise).unwrap_err().data.error_type,
            ErrorType::ProviderUnavailable
        );
        assert_eq!(
            replace_contents(|| raise(), || unreachable!()).effect,
            ComputerEffect::Unknown
        );
    }

    #[test]
    #[ignore = "requires the macOS pasteboard service; uses a private pasteboard"]
    fn clipboard_publication_exception_after_clear_is_partial() {
        autoreleasepool(|_| {
            let board = NSPasteboard::pasteboardWithUniqueName();
            assert!(board.setString_forType(&NSString::from_str("old"), unsafe {
                NSPasteboardTypeString
            }));
            let result = replace_contents(|| board.clearContents(), || raise());
            assert_eq!(result.effect, ComputerEffect::Partial);
            assert!(result.cleanup_complete);
            assert!(board.pasteboardItems().unwrap().is_empty());
            // The process and native service remain usable after the exception.
            assert!(board.setString_forType(&NSString::from_str("new"), unsafe {
                NSPasteboardTypeString
            }));
            // releaseGlobally is a legacy AppKit selector not exposed by objc2-app-kit.
            unsafe {
                let _: () = msg_send![&board, releaseGlobally];
            }
        });
    }
}
