//! Typed, owned ApplicationServices boundary. Copy/Create results are adopted
//! exactly once; heterogeneous values are checked before decoding.
use super::*;
use objc2_core_foundation::{CFBoolean, CFStringBuiltInEncodings, ConcreteType};

pub(super) fn status(code: AXError) -> Result<(), EIPError> {
    if code == AXError::Success {
        return Ok(());
    }
    let kind = match code {
        AXError::AttributeUnsupported
        | AXError::ActionUnsupported
        | AXError::ParameterizedAttributeUnsupported
        | AXError::NotImplemented => ErrorType::Unsupported,
        AXError::APIDisabled => ErrorType::Denied,
        AXError::IllegalArgument | AXError::InvalidUIElement => ErrorType::InvalidParams,
        _ => ErrorType::ProviderUnavailable,
    };
    let mut e = error(kind, "macOS accessibility native call failed");
    e.data.safe_detail = Some(format!("AXError {}", code.0));
    Err(e)
}
pub(super) fn code(e: &EIPError) -> Option<i32> {
    e.data
        .safe_detail
        .as_deref()?
        .strip_prefix("AXError ")?
        .parse()
        .ok()
}
fn adopt<T>(raw: *const T) -> Result<CFRetained<T>, EIPError>
where
    T: objc2_core_foundation::Type,
{
    let ptr = NonNull::new(raw.cast_mut()).ok_or_else(unavailable)?;
    // SAFETY: only successful Copy/Create out parameters enter this function.
    Ok(unsafe { CFRetained::from_raw(ptr) })
}
pub(super) fn typed<T: ConcreteType>(value: CFRetained<CFType>) -> Result<CFRetained<T>, EIPError> {
    value.downcast::<T>().map_err(|_| unavailable())
}
pub(super) fn get(n: &AXUIElement, key: &str, b: &Budget) -> Result<CFRetained<CFType>, EIPError> {
    let key = CFString::from_str(key);
    let mut raw = std::ptr::null();
    b.call(n, || unsafe {
        n.copy_attribute_value(&key, NonNull::from(&mut raw))
    })?;
    adopt(raw)
}
pub(super) fn parameter(
    n: &AXUIElement,
    key: &str,
    value: &CFType,
    b: &Budget,
) -> Result<CFRetained<CFType>, EIPError> {
    let key = CFString::from_str(key);
    let mut raw = std::ptr::null();
    b.call(n, || unsafe {
        n.copy_parameterized_attribute_value(&key, value, NonNull::from(&mut raw))
    })?;
    adopt(raw)
}
pub(super) fn count(n: &AXUIElement, key: &str, b: &Budget) -> Result<usize, EIPError> {
    let mut value = 0;
    b.call(n, || unsafe {
        n.attribute_value_count(&CFString::from_str(key), NonNull::from(&mut value))
    })?;
    usize::try_from(value).map_err(|_| unavailable())
}
pub(super) fn page(
    n: &AXUIElement,
    key: &str,
    start: usize,
    max: usize,
    b: &Budget,
) -> Result<CFRetained<CFArray>, EIPError> {
    let mut raw = std::ptr::null();
    let start = start.try_into().map_err(|_| invalid())?;
    let max = max.try_into().map_err(|_| invalid())?;
    b.call(n, || unsafe {
        n.copy_attribute_values(
            &CFString::from_str(key),
            start,
            max,
            NonNull::from(&mut raw),
        )
    })?;
    object_array(raw)
}
pub(super) fn names(
    n: &AXUIElement,
    kind: &str,
    b: &Budget,
) -> Result<CFRetained<CFArray>, EIPError> {
    let mut raw = std::ptr::null();
    b.call(n, || unsafe {
        match kind {
            "actions" => n.copy_action_names(NonNull::from(&mut raw)),
            "parameters" => n.copy_parameterized_attribute_names(NonNull::from(&mut raw)),
            _ => n.copy_attribute_names(NonNull::from(&mut raw)),
        }
    })?;
    object_array(raw)
}
fn object_array(
    raw: *const objc2_core_foundation::CFArray,
) -> Result<CFRetained<CFArray>, EIPError> {
    let array = adopt(raw)?;
    // SAFETY: AX CopyAttributeValues/Copy*Names return arrays of retained CF
    // objects. CFType permits heterogeneous values, decoded individually.
    Ok(unsafe { CFRetained::cast_unchecked(array) })
}
pub(super) fn array(value: &CFType) -> Result<CFRetained<CFArray>, EIPError> {
    let raw = value
        .downcast_ref::<objc2_core_foundation::CFArray>()
        .ok_or_else(unavailable)?;
    // SAFETY: the checked AX array owns CF objects. Generic CFType retains each
    // element without assuming its concrete type.
    Ok(unsafe { CFRetained::retain(NonNull::from(raw).cast::<CFArray>()) })
}
pub(super) fn writable(n: &AXUIElement, key: &str, b: &Budget) -> Result<bool, EIPError> {
    let mut value = 0;
    b.call(n, || unsafe {
        n.is_attribute_settable(&CFString::from_str(key), NonNull::from(&mut value))
    })?;
    Ok(value != 0)
}
pub(super) fn hit(
    n: &AXUIElement,
    p: CGPoint,
    b: &Budget,
) -> Result<CFRetained<AXUIElement>, EIPError> {
    if [p.x, p.y]
        .iter()
        .any(|v| !v.is_finite() || v.abs() > f32::MAX as f64)
    {
        return Err(invalid());
    }
    let mut raw = std::ptr::null();
    b.call(n, || unsafe {
        n.copy_element_at_position(p.x as f32, p.y as f32, NonNull::from(&mut raw))
    })?;
    adopt(raw)
}
pub(super) fn pid(n: &AXUIElement, b: &Budget) -> Result<i32, EIPError> {
    let mut pid = 0;
    b.call(n, || unsafe { n.pid(NonNull::from(&mut pid)) })?;
    Ok(pid)
}
/// Compare native protocol identifiers without applying a content text quota.
pub(super) fn string_is(v: &CFType, expected: &str) -> Result<bool, EIPError> {
    v.downcast_ref::<CFString>()
        .map(|v| v == &*CFString::from_str(expected))
        .ok_or_else(unavailable)
}
pub(super) fn boolean(v: &CFType) -> Result<bool, EIPError> {
    v.downcast_ref::<CFBoolean>()
        .map(|v| v.value())
        .ok_or_else(unavailable)
}
pub(super) fn integer(v: &CFType) -> Result<isize, EIPError> {
    v.downcast_ref::<CFNumber>()
        .and_then(CFNumber::as_isize)
        .ok_or_else(unavailable)
}
pub(super) fn numeric(v: &CFType) -> Result<f64, EIPError> {
    v.downcast_ref::<CFNumber>()
        .and_then(CFNumber::as_f64)
        .filter(|v| v.is_finite())
        .ok_or_else(unavailable)
}
/// Return decoded text and the number of native UTF-16 positions consumed.
/// CFStringGetBytes never splits a scalar while filling the bounded buffer.
pub(super) fn text(v: &CFType, b: &Budget) -> Result<(String, usize), EIPError> {
    let s = v.downcast_ref::<CFString>().ok_or_else(unavailable)?;
    let mut buf = vec![0; b.limit("max_text_bytes")];
    let mut used = 0;
    let consumed = unsafe {
        s.bytes(
            CFRange {
                location: 0,
                length: s.length(),
            },
            CFStringBuiltInEncodings::EncodingUTF8.0,
            0,
            false,
            buf.as_mut_ptr(),
            buf.len() as isize,
            &mut used,
        )
    };
    if consumed < s.length() {
        b.mark("byte_limit");
    }
    buf.truncate(usize::try_from(used).map_err(|_| unavailable())?);
    Ok((
        String::from_utf8(buf).map_err(|_| unavailable())?,
        consumed as usize,
    ))
}
/// Decode a byte-bounded suffix and its skipped native UTF-16 positions. The
/// temporary UTF-16 buffer is bounded independently of the source CFString size.
pub(super) fn text_suffix(v: &CFType, b: &Budget) -> Result<(String, usize), EIPError> {
    let value = v.downcast_ref::<CFString>().ok_or_else(unavailable)?;
    let length = usize::try_from(value.length()).map_err(|_| unavailable())?;
    let mut start = length.saturating_sub(b.limit("max_text_bytes"));
    if start < length
        && (0xdc00..=0xdfff).contains(&unsafe { value.character_at_index(start as isize) })
    {
        start += 1;
    }
    let mut units = vec![0u16; length - start];
    unsafe {
        value.characters(
            CFRange {
                location: start as isize,
                length: units.len() as isize,
            },
            units.as_mut_ptr(),
        );
    }
    let text = String::from_utf16(&units).map_err(|_| unavailable())?;
    let mut byte_start = text.len().saturating_sub(b.limit("max_text_bytes"));
    while !text.is_char_boundary(byte_start) {
        byte_start += 1;
    }
    start += text[..byte_start].encode_utf16().count();
    if start > 0 {
        b.mark("byte_limit");
    }
    Ok((text[byte_start..].to_owned(), start))
}
/// Native identifiers are protocol metadata, not user content. Keep them exact
/// under an independent fixed bound rather than truncating action/role names.
pub(super) fn identifier(v: &CFType) -> Result<String, EIPError> {
    let value = v.downcast_ref::<CFString>().ok_or_else(unavailable)?;
    if value.length() > 256 {
        return Err(error(
            ErrorType::QuotaExceeded,
            "AX identifier exceeds metadata bound",
        ));
    }
    Ok(value.to_string())
}
pub(super) fn name(v: &CFType, b: &Budget) -> Result<String, EIPError> {
    text(v, b).map(|v| v.0)
}

// Each pair fixes the Rust payload type to its AXValue discriminator. The
// pointers remain aligned and live for the synchronous native copy.
macro_rules! payload {
    ($decode:ident,$encode:ident,$ty:ty,$kind:ident) => {
        pub(super) fn $decode(v: &CFType) -> Result<$ty, EIPError> {
            let value = v.downcast_ref::<AXValue>().ok_or_else(unavailable)?;
            if unsafe { value.r#type() } != AXValueType::$kind {
                return Err(unavailable());
            }
            // SAFETY: these fixed AX payloads contain only C integers/floats.
            let mut out: $ty = unsafe { std::mem::zeroed() };
            if unsafe { value.value(AXValueType::$kind, NonNull::from(&mut out).cast()) } {
                Ok(out)
            } else {
                Err(unavailable())
            }
        }
        pub(super) fn $encode(mut value: $ty) -> Result<CFRetained<AXValue>, EIPError> {
            unsafe { AXValue::new(AXValueType::$kind, NonNull::from(&mut value).cast()) }
                .ok_or_else(unavailable)
        }
    };
}
payload!(point, point_value, CGPoint, CGPoint);
payload!(size, size_value, CGSize, CGSize);
pub(super) fn rectangle(v: &CFType) -> Result<CGRect, EIPError> {
    let value = v.downcast_ref::<AXValue>().ok_or_else(unavailable)?;
    if unsafe { value.r#type() } != AXValueType::CGRect {
        return Err(unavailable());
    }
    let mut out = CGRect::default();
    if unsafe { value.value(AXValueType::CGRect, NonNull::from(&mut out).cast()) } {
        Ok(out)
    } else {
        Err(unavailable())
    }
}
payload!(span, span_value, CFRange, CFRange);

pub(super) fn optional_selection<T>(value: Result<T, EIPError>) -> Result<Option<T>, EIPError> {
    match value {
        Err(e) if code(&e) == Some(AXError::NoValue.0) => Ok(None),
        value => value.map(Some),
    }
}
pub(super) fn optional<T>(value: Result<T, EIPError>) -> Result<Option<T>, EIPError> {
    match value {
        Ok(v) => Ok(Some(v)),
        Err(e) if e.data.error_type == ErrorType::Unsupported || code(&e) == Some(-25212) => {
            Ok(None)
        }
        Err(e) => Err(e),
    }
}
