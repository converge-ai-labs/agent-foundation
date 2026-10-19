//! Plain-text clipboard rules shared by the native desktop adapters.
use super::*;

pub(crate) const MAX_TEXT_BYTES: usize = 16_384;
pub(crate) const MAX_NATIVE_BYTES: usize = 128 * 1024;
// Serialize active native clipboard access across Sessions, not selection serving
// or keyboard input. External applications remain independent writers.
static ACCESS: Mutex<()> = Mutex::new(());

pub(super) fn gate(
    interrupted: &dyn Fn() -> bool,
) -> Result<std::sync::MutexGuard<'static, ()>, EIPError> {
    loop {
        check(interrupted)?;
        match ACCESS.try_lock() {
            Ok(guard) => return Ok(guard),
            Err(std::sync::TryLockError::Poisoned(error)) => return Ok(error.into_inner()),
            Err(std::sync::TryLockError::WouldBlock) => {
                std::thread::sleep(Duration::from_millis(5))
            }
        }
    }
}

pub(super) fn check(interrupted: &dyn Fn() -> bool) -> Result<(), EIPError> {
    if interrupted() {
        Err(error(
            ErrorType::Cancelled,
            "clipboard operation interrupted",
        ))
    } else {
        Ok(())
    }
}

pub(super) fn write_text(text: &str) -> Result<String, EIPError> {
    if text.len() > MAX_TEXT_BYTES || text.contains('\0') {
        return Err(error(
            ErrorType::InvalidParams,
            "clipboard text must contain at most 16384 UTF-8 bytes and no NUL",
        ));
    }
    Ok(text.replace("\r\n", "\n"))
}

pub(super) fn text(text: String, max_bytes: u32) -> Result<ComputerReadClipboardResult, EIPError> {
    // CRLF normalization can at most halve the bytes. Reject before allocating
    // another buffer when even that best case cannot fit the response.
    if text.len() > max_bytes as usize * 2 {
        return Err(oversize());
    }
    let text = text.replace("\r\n", "\n");
    if text.len() > max_bytes as usize {
        return Err(oversize());
    }
    if text.contains('\0') {
        return Err(invalid_text());
    }
    Ok(ComputerReadClipboardResult {
        status: ComputerClipboardStatus::Text,
        text: Some(text),
    })
}

pub(super) fn empty(status: ComputerClipboardStatus) -> ComputerReadClipboardResult {
    ComputerReadClipboardResult { status, text: None }
}

pub(super) fn oversize() -> EIPError {
    error(
        ErrorType::QuotaExceeded,
        "clipboard content exceeds the byte limit",
    )
}

pub(super) fn invalid_text() -> EIPError {
    error(
        ErrorType::ProviderUnavailable,
        "clipboard contains invalid Unicode text",
    )
}

pub(super) fn effect(effect: ComputerEffect) -> Effect {
    Effect {
        effect,
        cleanup_complete: true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_rules_preserve_empty_and_standalone_cr() {
        assert_eq!(write_text("").unwrap(), "");
        assert_eq!(write_text("a\r\nb\rc\n").unwrap(), "a\nb\rc\n");
        assert_eq!(text(String::new(), 1).unwrap().text, Some(String::new()));
        assert!(write_text("a\0b").is_err());
        assert!(write_text(&"é".repeat(8193)).is_err());
        assert!(text("é".into(), 1).is_err());
        assert_eq!(text("\r\n".into(), 1).unwrap().text.as_deref(), Some("\n"));
    }
}
