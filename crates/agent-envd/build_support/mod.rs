use std::collections::{BTreeMap, BTreeSet};

use heck::{ToShoutySnakeCase, ToSnakeCase, ToUpperCamelCase};
use prost_reflect::{
    DescriptorPool, DynamicMessage, EnumDescriptor, ExtensionDescriptor, FieldDescriptor, Kind,
    MessageDescriptor, MethodDescriptor, ReflectMessage,
};

const PACKAGE: &str = "a13n.agent_envd.eip.v1";
const TOOLING_MESSAGES: &[&str] = &[
    "EIPMethodOptions",
    "EIPMessageOptions",
    "EIPEnumValueOptions",
    "EIPFieldOptions",
    "EIPDataFrameProfileOptions",
];
const TOOLING_ENUMS: &[&str] = &[
    "MethodKind",
    "ReplayClass",
    "ErrorFamily",
    "EIPStringFormat",
    "TransferAction",
    "TransferDirection",
    "EIPDataFrameKind",
    "EIPDataResetStatus",
];

struct Extensions {
    file: ExtensionDescriptor,
    method: ExtensionDescriptor,
    message: ExtensionDescriptor,
    enum_value: ExtensionDescriptor,
    field: ExtensionDescriptor,
}

#[derive(Clone)]
struct MethodRecord {
    rpc_name: String,
    rust_name: String,
    jsonrpc_method: String,
    kind: String,
    replay_class: String,
    introduced: String,
    error_family: String,
    transfer_action: Option<String>,
    transfer_direction: Option<String>,
    params_type: String,
    result_type: Option<String>,
}

pub fn render(pool: &DescriptorPool) -> Result<String, String> {
    let extensions = Extensions {
        file: extension(pool, "eip_data_frame_profile")?,
        method: extension(pool, "eip_method")?,
        message: extension(pool, "eip_message")?,
        enum_value: extension(pool, "eip_enum_value")?,
        field: extension(pool, "eip_field")?,
    };
    let mut output = String::from(
        "// Generated from protocol/eip/v1/descriptor.pb. DO NOT EDIT.\n\
         use std::collections::{BTreeMap, BTreeSet};\n\
         use serde::{Deserialize, Serialize};\n\n",
    );
    output.push_str(&format!(
        "pub const EIP_PROTOCOL_VERSION: &str = \"1.0\";\n\
         pub const EIP_PROTO_PACKAGE: &str = \"{PACKAGE}\";\n\n"
    ));
    render_data_frame(&mut output, pool, &extensions)?;
    output.push_str(
        "#[derive(Debug, Clone, PartialEq, Eq)]\n\
         pub struct ValidationError(pub String);\n\n\
         pub trait EipValidate {\n\
         \x20   fn validate(&self) -> Result<(), ValidationError>;\n\
         }\n\n\
         #[derive(Debug)]\n\
         pub enum DecodeError {\n\
         \x20   Json(serde_json::Error),\n\
         \x20   Validation(ValidationError),\n\
         }\n\n\
         pub fn decode<T>(payload: &str) -> Result<T, DecodeError>\n\
         where\n\
         \x20   T: serde::de::DeserializeOwned + EipValidate,\n\
         {\n\
         \x20   let mut duplicate_checker = serde_json::Deserializer::from_str(payload);\n\
         \x20   UniqueJson::deserialize(&mut duplicate_checker).map_err(DecodeError::Json)?;\n\
         \x20   duplicate_checker.end().map_err(DecodeError::Json)?;\n\
         \x20   let value: T = serde_json::from_str(payload).map_err(DecodeError::Json)?;\n\
         \x20   value.validate().map_err(DecodeError::Validation)?;\n\
         \x20   Ok(value)\n\
         }\n\n\
         #[derive(Debug)]\n\
         pub enum EncodeError {\n\
         \x20   Validation(ValidationError),\n\
         \x20   Json(serde_json::Error),\n\
         }\n\n\
         pub fn encode<T: Serialize + EipValidate>(value: &T) -> Result<Vec<u8>, EncodeError> {\n\
         \x20   value.validate().map_err(EncodeError::Validation)?;\n\
         \x20   let canonical = serde_json::to_value(value).map_err(EncodeError::Json)?;\n\
         \x20   serde_json::to_vec(&canonical).map_err(EncodeError::Json)\n\
         }\n\n\
         fn validate_absolute_path(value: &str) -> bool {\n\
         \x20   value.starts_with('/')\n\
         \x20       && !value.contains('\\0')\n\
         \x20       && (value == \"/\" || (!value.ends_with('/') && value.split('/').skip(1).all(|part| !matches!(part, \"\" | \".\" | \"..\"))))\n\
         }\n\n\
         fn validate_protocol_version(value: &str) -> bool {\n\
         \x20   let Some((major, minor)) = value.split_once('.') else { return false; };\n\
         \x20   !major.starts_with('0') && major.parse::<u64>().is_ok() && minor.parse::<u64>().is_ok()\n\
         }\n\n\
         fn validate_base64_unpadded(value: &str) -> bool {\n\
         \x20   use base64::Engine as _;\n\
         \x20   !value.contains('=')\n\
         \x20       && base64::engine::general_purpose::STANDARD_NO_PAD.decode(value).is_ok()\n\
         }\n\n\
         fn validate_sha256(value: &str) -> bool {\n\
         \x20   value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))\n\
         }\n\n"
    );
    output.push_str(
        r#"struct UniqueJson;

impl<'de> Deserialize<'de> for UniqueJson {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_any(UniqueJsonVisitor)
    }
}

struct UniqueJsonVisitor;

impl<'de> serde::de::Visitor<'de> for UniqueJsonVisitor {
    type Value = UniqueJson;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("a JSON value without duplicate object keys")
    }

    fn visit_bool<E>(self, _value: bool) -> Result<Self::Value, E> {
        Ok(UniqueJson)
    }

    fn visit_i64<E>(self, _value: i64) -> Result<Self::Value, E> {
        Ok(UniqueJson)
    }

    fn visit_u64<E>(self, _value: u64) -> Result<Self::Value, E> {
        Ok(UniqueJson)
    }

    fn visit_f64<E>(self, _value: f64) -> Result<Self::Value, E> {
        Ok(UniqueJson)
    }

    fn visit_str<E>(self, _value: &str) -> Result<Self::Value, E> {
        Ok(UniqueJson)
    }

    fn visit_string<E>(self, _value: String) -> Result<Self::Value, E> {
        Ok(UniqueJson)
    }

    fn visit_none<E>(self) -> Result<Self::Value, E> {
        Ok(UniqueJson)
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(UniqueJson)
    }

    fn visit_some<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        UniqueJson::deserialize(deserializer)
    }

    fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
    where
        A: serde::de::SeqAccess<'de>,
    {
        while sequence.next_element::<UniqueJson>()?.is_some() {}
        Ok(UniqueJson)
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: serde::de::MapAccess<'de>,
    {
        let mut keys = BTreeSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if !keys.insert(key.clone()) {
                return Err(serde::de::Error::custom(format!("duplicate JSON field: {key}")));
            }
            map.next_value::<UniqueJson>()?;
        }
        Ok(UniqueJson)
    }
}

"#,
    );

    let mut enums: Vec<_> = pool
        .all_enums()
        .filter(|item| item.package_name() == PACKAGE && !TOOLING_ENUMS.contains(&item.name()))
        .collect();
    enums.sort_by(|left, right| left.full_name().cmp(right.full_name()));
    for item in enums {
        render_enum(&mut output, &item, &extensions)?;
    }

    let mut messages: Vec<_> = pool
        .all_messages()
        .filter(|item| {
            item.package_name() == PACKAGE
                && !item.is_map_entry()
                && !TOOLING_MESSAGES.contains(&item.name())
        })
        .collect();
    messages.sort_by(|left, right| left.full_name().cmp(right.full_name()));
    let defaulted_messages = defaulted_message_types(&messages, &extensions)?;
    for item in messages {
        render_message(&mut output, &item, &extensions, &defaulted_messages)?;
    }
    render_jsonrpc_envelopes(&mut output);

    let mut methods = method_records(pool, &extensions)?;
    methods.sort_by(|left, right| left.jsonrpc_method.cmp(&right.jsonrpc_method));
    render_registry(&mut output, &methods);
    render_dispatch(&mut output, &methods);
    Ok(output)
}

fn extension(pool: &DescriptorPool, name: &str) -> Result<ExtensionDescriptor, String> {
    pool.get_extension_by_name(&format!("{PACKAGE}.{name}"))
        .ok_or_else(|| format!("descriptor is missing {name} extension"))
}

fn numeric_enum_values(
    pool: &DescriptorPool,
    name: &str,
    prefix: &str,
) -> Result<Vec<(String, i32)>, String> {
    let descriptor = pool
        .get_enum_by_name(&format!("{PACKAGE}.{name}"))
        .ok_or_else(|| format!("descriptor is missing {name}"))?;
    Ok(descriptor
        .values()
        .filter(|value| value.number() != 0)
        .map(|value| {
            (
                value
                    .name()
                    .strip_prefix(prefix)
                    .unwrap_or(value.name())
                    .to_upper_camel_case(),
                value.number(),
            )
        })
        .collect())
}

fn render_data_frame(
    output: &mut String,
    pool: &DescriptorPool,
    extensions: &Extensions,
) -> Result<(), String> {
    let candidates = pool
        .files()
        .filter_map(|file| {
            let options = file.options();
            options
                .has_extension(&extensions.file)
                .then(|| extension_message(options, &extensions.file))
        })
        .collect::<Result<Vec<_>, _>>()?;
    if candidates.len() != 1 {
        return Err(format!(
            "expected exactly one EIP data-frame profile, found {}",
            candidates.len()
        ));
    }
    let profile = &candidates[0];
    let magic = string_field(profile, "magic")?;
    let version = u32_field(profile, "profile_version")?;
    let eip_major = u32_field(profile, "eip_major")?;
    let header_bytes = u32_field(profile, "header_bytes")?;
    let widths = [
        u32_field(profile, "magic_bytes")?,
        u32_field(profile, "version_bytes")?,
        u32_field(profile, "kind_bytes")?,
        u32_field(profile, "status_bytes")?,
        u32_field(profile, "handle_length_bytes")?,
        u32_field(profile, "reserved_bytes")?,
        u32_field(profile, "stream_offset_bytes")?,
        u32_field(profile, "payload_length_bytes")?,
    ];
    if magic != "EIPD"
        || version != 1
        || eip_major != 1
        || header_bytes != 24
        || widths != [4, 1, 1, 2, 2, 2, 8, 4]
        || widths.iter().sum::<u32>() != header_bytes
    {
        return Err("EIP data-frame profile has an incompatible header layout".to_owned());
    }
    let kinds = numeric_enum_values(pool, "EIPDataFrameKind", "EIP_DATA_FRAME_KIND_")?;
    let statuses = numeric_enum_values(pool, "EIPDataResetStatus", "EIP_DATA_RESET_STATUS_")?;
    let expected_kinds = vec![
        ("Attach".to_owned(), 1),
        ("Attached".to_owned(), 2),
        ("Chunk".to_owned(), 3),
        ("End".to_owned(), 4),
        ("EndAck".to_owned(), 5),
        ("Reset".to_owned(), 6),
    ];
    let expected_statuses = vec![
        ("Protocol".to_owned(), 1),
        ("Denied".to_owned(), 2),
        ("Expired".to_owned(), 3),
        ("Source".to_owned(), 4),
        ("Limit".to_owned(), 5),
        ("Cancelled".to_owned(), 6),
        ("Internal".to_owned(), 7),
    ];
    if kinds != expected_kinds || statuses != expected_statuses {
        return Err("EIP data-frame enum mapping is incompatible with profile 1".to_owned());
    }

    output.push_str(&format!(
        "pub const EIP_DATA_FRAME_MAGIC: [u8; 4] = *b\"{magic}\";\n\
         pub const EIP_DATA_FRAME_PROFILE_VERSION: u8 = {version};\n\
         pub const EIP_DATA_FRAME_EIP_MAJOR: u8 = {eip_major};\n\
         pub const EIP_DATA_FRAME_HEADER_BYTES: usize = {header_bytes};\n\
         pub const EIP_DATA_FRAME_MAX_HANDLE_BYTES: usize = u16::MAX as usize;\n\
         pub const EIP_DATA_FRAME_MAX_PAYLOAD_BYTES: usize = u32::MAX as usize;\n\n"
    ));
    output.push_str(
        "#[derive(Debug, Clone, Copy, PartialEq, Eq)]\n#[repr(u8)]\npub enum DataFrameKind {\n",
    );
    for (name, number) in &kinds {
        output.push_str(&format!("    {name} = {number},\n"));
    }
    output.push_str("}\n\nimpl TryFrom<u8> for DataFrameKind {\n    type Error = DataFrameCodecError;\n    fn try_from(value: u8) -> Result<Self, Self::Error> {\n        match value {\n");
    for (name, number) in &kinds {
        output.push_str(&format!("            {number} => Ok(Self::{name}),\n"));
    }
    output.push_str("            _ => Err(DataFrameCodecError(\"unknown EIP data-frame kind\".to_owned())),\n        }\n    }\n}\n\n");

    output.push_str(
        "#[derive(Debug, Clone, Copy, PartialEq, Eq)]\n#[repr(u16)]\npub enum DataResetStatus {\n",
    );
    for (name, number) in &statuses {
        output.push_str(&format!("    {name} = {number},\n"));
    }
    output.push_str("}\n\nimpl TryFrom<u16> for DataResetStatus {\n    type Error = DataFrameCodecError;\n    fn try_from(value: u16) -> Result<Self, Self::Error> {\n        match value {\n");
    for (name, number) in &statuses {
        output.push_str(&format!("            {number} => Ok(Self::{name}),\n"));
    }
    output.push_str("            _ => Err(DataFrameCodecError(\"RESET has an invalid terminal status\".to_owned())),\n        }\n    }\n}\n\n");

    output.push_str(
        r#"#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DataFrameCodecError(pub String);

impl std::fmt::Display for DataFrameCodecError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for DataFrameCodecError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DataFrame {
    pub kind: DataFrameKind,
    pub handle: String,
    pub offset: u64,
    pub payload: Vec<u8>,
    pub reset_status: Option<DataResetStatus>,
}

fn validate_data_frame(frame: &DataFrame, max_frame_bytes: usize) -> Result<(Vec<u8>, u16), DataFrameCodecError> {
    let handle = frame.handle.as_bytes();
    if handle.is_empty() {
        return Err(DataFrameCodecError("EIP data-frame handle must be non-empty UTF-8".to_owned()));
    }
    if handle.len() > EIP_DATA_FRAME_MAX_HANDLE_BYTES {
        return Err(DataFrameCodecError("EIP data-frame handle is too long".to_owned()));
    }
    if frame.payload.len() > EIP_DATA_FRAME_MAX_PAYLOAD_BYTES {
        return Err(DataFrameCodecError("EIP data-frame payload is too long".to_owned()));
    }
    let status = if frame.kind == DataFrameKind::Reset {
        frame.reset_status
            .map(|value| value as u16)
            .ok_or_else(|| DataFrameCodecError("RESET requires a terminal status".to_owned()))?
    } else {
        if frame.reset_status.is_some() {
            return Err(DataFrameCodecError("terminal status is valid only for RESET".to_owned()));
        }
        0
    };
    if frame.kind != DataFrameKind::Chunk && !frame.payload.is_empty() {
        return Err(DataFrameCodecError("payload is valid only for CHUNK".to_owned()));
    }
    if frame.kind == DataFrameKind::Chunk
        && frame.offset.checked_add(frame.payload.len() as u64).is_none()
    {
        return Err(DataFrameCodecError("EIP data-frame payload overflows the stream offset".to_owned()));
    }
    if matches!(frame.kind, DataFrameKind::Attach | DataFrameKind::Attached) && frame.offset != 0 {
        return Err(DataFrameCodecError("attachment frames must use offset zero".to_owned()));
    }
    if max_frame_bytes < EIP_DATA_FRAME_HEADER_BYTES + 1 {
        return Err(DataFrameCodecError("invalid EIP data-frame ceiling".to_owned()));
    }
    let total = EIP_DATA_FRAME_HEADER_BYTES
        .checked_add(handle.len())
        .and_then(|value| value.checked_add(frame.payload.len()))
        .ok_or_else(|| DataFrameCodecError("EIP data frame length overflows usize".to_owned()))?;
    if total > max_frame_bytes {
        return Err(DataFrameCodecError("EIP data frame exceeds the configured ceiling".to_owned()));
    }
    Ok((handle.to_vec(), status))
}

pub fn encode_data_frame(frame: &DataFrame, max_frame_bytes: usize) -> Result<Vec<u8>, DataFrameCodecError> {
    let (handle, status) = validate_data_frame(frame, max_frame_bytes)?;
    let mut output = Vec::with_capacity(EIP_DATA_FRAME_HEADER_BYTES + handle.len() + frame.payload.len());
    output.extend_from_slice(&EIP_DATA_FRAME_MAGIC);
    output.push(EIP_DATA_FRAME_PROFILE_VERSION);
    output.push(frame.kind as u8);
    output.extend_from_slice(&status.to_be_bytes());
    output.extend_from_slice(&(handle.len() as u16).to_be_bytes());
    output.extend_from_slice(&0u16.to_be_bytes());
    output.extend_from_slice(&frame.offset.to_be_bytes());
    output.extend_from_slice(&(frame.payload.len() as u32).to_be_bytes());
    output.extend_from_slice(&handle);
    output.extend_from_slice(&frame.payload);
    Ok(output)
}

pub fn decode_data_frame(payload: &[u8], max_frame_bytes: usize) -> Result<DataFrame, DataFrameCodecError> {
    if max_frame_bytes < EIP_DATA_FRAME_HEADER_BYTES + 1 {
        return Err(DataFrameCodecError("invalid EIP data-frame ceiling".to_owned()));
    }
    if payload.len() > max_frame_bytes {
        return Err(DataFrameCodecError("EIP data frame exceeds the configured ceiling".to_owned()));
    }
    if payload.len() < EIP_DATA_FRAME_HEADER_BYTES {
        return Err(DataFrameCodecError("EIP data frame is shorter than its header".to_owned()));
    }
    if payload[..4] != EIP_DATA_FRAME_MAGIC {
        return Err(DataFrameCodecError("invalid EIP data-frame magic".to_owned()));
    }
    if payload[4] != EIP_DATA_FRAME_PROFILE_VERSION {
        return Err(DataFrameCodecError("unsupported EIP data-frame profile version".to_owned()));
    }
    let kind = DataFrameKind::try_from(payload[5])?;
    let status_value = u16::from_be_bytes([payload[6], payload[7]]);
    let handle_length = u16::from_be_bytes([payload[8], payload[9]]) as usize;
    if payload[10] != 0 || payload[11] != 0 {
        return Err(DataFrameCodecError("EIP data-frame reserved field must be zero".to_owned()));
    }
    let offset = u64::from_be_bytes(payload[12..20].try_into().expect("fixed-width offset"));
    let payload_length = u32::from_be_bytes(payload[20..24].try_into().expect("fixed-width payload length")) as usize;
    let expected = EIP_DATA_FRAME_HEADER_BYTES
        .checked_add(handle_length)
        .and_then(|value| value.checked_add(payload_length))
        .ok_or_else(|| DataFrameCodecError("EIP data-frame body length overflow".to_owned()))?;
    if expected != payload.len() {
        return Err(DataFrameCodecError("EIP data-frame body length mismatch".to_owned()));
    }
    let handle_end = EIP_DATA_FRAME_HEADER_BYTES + handle_length;
    let handle = std::str::from_utf8(&payload[EIP_DATA_FRAME_HEADER_BYTES..handle_end])
        .map_err(|_| DataFrameCodecError("EIP data-frame handle must be non-empty UTF-8".to_owned()))?
        .to_owned();
    let reset_status = if kind == DataFrameKind::Reset {
        Some(DataResetStatus::try_from(status_value)?)
    } else {
        if status_value != 0 {
            return Err(DataFrameCodecError("terminal status is valid only for RESET".to_owned()));
        }
        None
    };
    let frame = DataFrame {
        kind,
        handle,
        offset,
        payload: payload[handle_end..].to_vec(),
        reset_status,
    };
    validate_data_frame(&frame, max_frame_bytes)?;
    Ok(frame)
}

"#,
    );
    Ok(())
}

fn extension_message(
    options: DynamicMessage,
    extension: &ExtensionDescriptor,
) -> Result<DynamicMessage, String> {
    if !options.has_extension(extension) {
        return Err(format!("missing {} option", extension.full_name()));
    }
    options
        .get_extension(extension)
        .as_message()
        .cloned()
        .ok_or_else(|| format!("{} option is not a message", extension.full_name()))
}

fn string_field(message: &DynamicMessage, name: &str) -> Result<String, String> {
    message
        .get_field_by_name(name)
        .and_then(|value| value.as_str().map(str::to_owned))
        .ok_or_else(|| format!("option field {name} is not a string"))
}

fn bool_field(message: &DynamicMessage, name: &str) -> bool {
    message
        .get_field_by_name(name)
        .and_then(|value| value.as_bool())
        .unwrap_or(false)
}

fn u32_field(message: &DynamicMessage, name: &str) -> Result<u32, String> {
    message
        .get_field_by_name(name)
        .and_then(|value| value.as_u32())
        .ok_or_else(|| format!("option field {name} is not a uint32"))
}

fn i32_field(message: &DynamicMessage, name: &str) -> Result<i32, String> {
    message
        .get_field_by_name(name)
        .and_then(|value| value.as_i32())
        .ok_or_else(|| format!("option field {name} is not an int32"))
}

fn enum_field_name(message: &DynamicMessage, name: &str) -> Result<String, String> {
    let field = message
        .descriptor()
        .get_field_by_name(name)
        .ok_or_else(|| format!("option field {name} is missing"))?;
    let number = message
        .get_field(&field)
        .as_enum_number()
        .ok_or_else(|| format!("option field {name} is not an enum"))?;
    let Kind::Enum(enum_type) = field.kind() else {
        return Err(format!("option field {name} has the wrong descriptor type"));
    };
    enum_type
        .get_value(number)
        .map(|value| value.name().to_owned())
        .ok_or_else(|| format!("option field {name} has unknown value {number}"))
}

fn render_enum(
    output: &mut String,
    descriptor: &EnumDescriptor,
    extensions: &Extensions,
) -> Result<(), String> {
    output.push_str("#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]\n");
    output.push_str(&format!("pub enum {} {{\n", descriptor.name()));
    let prefix = format!("{}_", descriptor.name().to_shouty_snake_case());
    let mut rendered = 0;
    let mut error_codes = Vec::new();
    let mut used_error_codes = BTreeSet::new();
    for value in descriptor.values() {
        let options = value.options();
        if !options.has_extension(&extensions.enum_value) {
            continue;
        }
        let option = extension_message(options, &extensions.enum_value)?;
        let json_name = string_field(&option, "json_name")?;
        if json_name.is_empty() {
            continue;
        }
        let variant = value
            .name()
            .strip_prefix(&prefix)
            .unwrap_or(value.name())
            .to_upper_camel_case();
        output.push_str(&format!(
            "    #[serde(rename = \"{json_name}\")]\n    {variant},\n"
        ));
        if descriptor.name() == "ErrorType" {
            if !option.has_field_by_name("error_code") {
                return Err(format!(
                    "EIP error type {} has no JSON-RPC code",
                    value.name()
                ));
            }
            let error_code = i32_field(&option, "error_code")?;
            if !used_error_codes.insert(error_code) {
                return Err(format!("duplicate EIP JSON-RPC error code: {error_code}"));
            }
            error_codes.push((variant, error_code));
        }
        rendered += 1;
    }
    if rendered == 0 {
        return Err(format!(
            "wire enum {} has no JSON values",
            descriptor.full_name()
        ));
    }
    output.push_str("}\n\n");
    if descriptor.name() == "ErrorType" {
        output.push_str(
            "impl ErrorType {\n    pub const fn code(self) -> i32 {\n        match self {\n",
        );
        for (variant, code) in error_codes {
            output.push_str(&format!("            Self::{variant} => {code},\n"));
        }
        output.push_str("        }\n    }\n}\n\n");
    }
    Ok(())
}

fn message_flags(
    descriptor: &MessageDescriptor,
    extensions: &Extensions,
) -> Result<(bool, bool), String> {
    let options = descriptor.options();
    if !options.has_extension(&extensions.message) {
        return Ok((false, false));
    }
    let option = extension_message(options, &extensions.message)?;
    Ok((
        bool_field(&option, "transparent"),
        bool_field(&option, "discriminated_union"),
    ))
}

fn field_option_message(
    field: &FieldDescriptor,
    extensions: &Extensions,
) -> Result<Option<DynamicMessage>, String> {
    let options = field.options();
    if !options.has_extension(&extensions.field) {
        return Ok(None);
    }
    extension_message(options, &extensions.field).map(Some)
}

fn field_has_explicit_default(
    field: &FieldDescriptor,
    extensions: &Extensions,
) -> Result<bool, String> {
    let Some(option) = field_option_message(field, extensions)? else {
        return Ok(false);
    };
    Ok(option.has_field_by_name("default_bool")
        || option.has_field_by_name("default_uint")
        || option.has_field_by_name("default_string")
        || bool_field(&option, "default_message"))
}

fn defaulted_message_types(
    messages: &[MessageDescriptor],
    extensions: &Extensions,
) -> Result<BTreeSet<String>, String> {
    let mut defaulted = BTreeSet::new();
    for message in messages {
        for field in message.fields() {
            let Some(option) = field_option_message(&field, extensions)? else {
                continue;
            };
            if bool_field(&option, "default_message") {
                let Kind::Message(value_type) = field.kind() else {
                    return Err(format!(
                        "default_message field {} is not a message",
                        field.full_name()
                    ));
                };
                defaulted.insert(value_type.full_name().to_owned());
            }
        }
    }
    Ok(defaulted)
}

fn enum_variant_for_json(
    descriptor: &EnumDescriptor,
    json_name: &str,
    extensions: &Extensions,
) -> Result<String, String> {
    let prefix = format!("{}_", descriptor.name().to_shouty_snake_case());
    for value in descriptor.values() {
        let options = value.options();
        if !options.has_extension(&extensions.enum_value) {
            continue;
        }
        let option = extension_message(options, &extensions.enum_value)?;
        if string_field(&option, "json_name")? == json_name {
            return Ok(value
                .name()
                .strip_prefix(&prefix)
                .unwrap_or(value.name())
                .to_upper_camel_case());
        }
    }
    Err(format!(
        "enum {} has no JSON value {json_name}",
        descriptor.full_name()
    ))
}

fn render_field_default(
    output: &mut String,
    owner: &MessageDescriptor,
    field: &FieldDescriptor,
    extensions: &Extensions,
) -> Result<Option<String>, String> {
    let Some(option) = field_option_message(field, extensions)? else {
        return Ok(None);
    };
    let default_fields = [
        option.has_field_by_name("default_bool"),
        option.has_field_by_name("default_uint"),
        option.has_field_by_name("default_string"),
        bool_field(&option, "default_message"),
    ];
    let count = default_fields
        .into_iter()
        .filter(|present| *present)
        .count();
    if count == 0 {
        return Ok(None);
    }
    if count != 1 {
        return Err(format!(
            "field {} must declare at most one EIP default",
            field.full_name()
        ));
    }

    let field_type = rust_base_type(field)?;
    let expression = if option.has_field_by_name("default_bool") {
        option
            .get_field_by_name("default_bool")
            .and_then(|value| value.as_bool())
            .ok_or_else(|| "default_bool is not bool".to_owned())?
            .to_string()
    } else if option.has_field_by_name("default_uint") {
        option
            .get_field_by_name("default_uint")
            .and_then(|value| value.as_u64())
            .ok_or_else(|| "default_uint is not uint64".to_owned())?
            .to_string()
    } else if option.has_field_by_name("default_string") {
        let value = string_field(&option, "default_string")?;
        match field.kind() {
            Kind::Enum(descriptor) => {
                format!(
                    "{}::{}",
                    descriptor.name(),
                    enum_variant_for_json(&descriptor, &value, extensions)?
                )
            }
            Kind::String => format!("{value:?}.to_owned()"),
            _ => {
                return Err(format!(
                    "default_string field {} is not a string or enum",
                    field.full_name()
                ));
            }
        }
    } else {
        format!("{field_type}::default()")
    };
    let function = format!(
        "default_{}_{}",
        owner.name().to_snake_case(),
        field.name().to_snake_case()
    );
    output.push_str(&format!(
        "fn {function}() -> {field_type} {{ {expression} }}\n\
         fn is_{function}(value: &{field_type}) -> bool {{ value == &{function}() }}\n\n"
    ));
    Ok(Some(function))
}

fn real_oneof_fields(descriptor: &MessageDescriptor) -> Vec<Vec<FieldDescriptor>> {
    descriptor
        .oneofs()
        .filter_map(|oneof| {
            let fields: Vec<_> = oneof
                .fields()
                .filter(|field| !field.field_descriptor_proto().proto3_optional())
                .collect();
            (!fields.is_empty()).then_some(fields)
        })
        .collect()
}

fn render_message(
    output: &mut String,
    descriptor: &MessageDescriptor,
    extensions: &Extensions,
    defaulted_messages: &BTreeSet<String>,
) -> Result<(), String> {
    let (transparent, discriminated_union) = message_flags(descriptor, extensions)?;
    let mut fields: Vec<_> = descriptor.fields().collect();
    fields.sort_by_key(FieldDescriptor::number);
    if transparent {
        if fields.len() != 1 {
            return Err(format!(
                "transparent message {} must have one field",
                descriptor.full_name()
            ));
        }
        output.push_str("#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]\n");
        output.push_str("#[serde(transparent)]\n");
        output.push_str(&format!(
            "pub struct {}(pub {});\n\n",
            descriptor.name(),
            rust_base_type(&fields[0])?
        ));
        render_validation_impl(output, descriptor, &fields, extensions, true, false)?;
        return Ok(());
    }
    if discriminated_union {
        let oneofs = real_oneof_fields(descriptor);
        if oneofs.len() != 1 {
            return Err(format!(
                "union {} must have one real oneof",
                descriptor.full_name()
            ));
        }
        output.push_str("#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]\n");
        output.push_str("#[serde(untagged)]\n");
        output.push_str(&format!("pub enum {} {{\n", descriptor.name()));
        for field in &oneofs[0] {
            output.push_str(&format!(
                "    {}({}),\n",
                field.name().to_upper_camel_case(),
                rust_base_type(field)?
            ));
        }
        output.push_str("}\n\n");
        render_validation_impl(output, descriptor, &fields, extensions, false, true)?;
        return Ok(());
    }

    let mut default_functions = BTreeMap::new();
    for field in &fields {
        if let Some(function) = render_field_default(output, descriptor, field, extensions)? {
            default_functions.insert(field.full_name().to_owned(), function);
        }
    }
    if defaulted_messages.contains(descriptor.full_name()) {
        output.push_str("#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]\n");
    } else {
        output.push_str("#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]\n");
    }
    output.push_str("#[serde(deny_unknown_fields)]\n");
    output.push_str(&format!("pub struct {} {{\n", descriptor.name()));
    let real_oneof_names: BTreeSet<_> = real_oneof_fields(descriptor)
        .into_iter()
        .flatten()
        .map(|field| field.full_name().to_owned())
        .collect();
    for field in &fields {
        let explicit_default = default_functions.get(field.full_name());
        let optional = (field.field_descriptor_proto().proto3_optional()
            || real_oneof_names.contains(field.full_name()))
            && explicit_default.is_none();
        let (field_type, default) = rust_field_type(field, optional)?;
        if let Some(function) = explicit_default {
            output.push_str(&format!(
                "    #[serde(default = \"{function}\", skip_serializing_if = \"is_{function}\")]\n"
            ));
        } else if field.is_map() {
            output
                .push_str("    #[serde(default, skip_serializing_if = \"BTreeMap::is_empty\")]\n");
        } else if field.is_list() {
            output.push_str("    #[serde(default, skip_serializing_if = \"Vec::is_empty\")]\n");
        } else if default {
            output.push_str("    #[serde(default)]\n");
        }
        if optional {
            output.push_str("    #[serde(default, skip_serializing_if = \"Option::is_none\")]\n");
        }
        output.push_str(&format!("    pub {}: {},\n", field.name(), field_type));
    }
    output.push_str("}\n\n");
    render_validation_impl(output, descriptor, &fields, extensions, false, false)?;
    Ok(())
}

fn render_validation_impl(
    output: &mut String,
    descriptor: &MessageDescriptor,
    fields: &[FieldDescriptor],
    extensions: &Extensions,
    transparent: bool,
    discriminated_union: bool,
) -> Result<(), String> {
    output.push_str(&format!(
        "impl EipValidate for {} {{\n    fn validate(&self) -> Result<(), ValidationError> {{\n",
        descriptor.name()
    ));
    if discriminated_union {
        output.push_str("        match self {\n");
        for oneof in real_oneof_fields(descriptor) {
            for field in oneof {
                output.push_str(&format!(
                    "            Self::{}(value) => value.validate(),\n",
                    field.name().to_upper_camel_case()
                ));
            }
        }
        output.push_str("        }\n    }\n}\n\n");
        return Ok(());
    }
    if transparent {
        render_field_checks(output, &fields[0], "self.0", false, extensions, 8)?;
    } else {
        let real_oneofs = real_oneof_fields(descriptor);
        let real_oneof_names: BTreeSet<_> = real_oneofs
            .iter()
            .flatten()
            .map(|field| field.full_name().to_owned())
            .collect();
        for field in fields {
            let optional = (field.field_descriptor_proto().proto3_optional()
                || real_oneof_names.contains(field.full_name()))
                && !field_has_explicit_default(field, extensions)?;
            let expression = format!("self.{}", field.name());
            render_field_checks(output, field, &expression, optional, extensions, 8)?;
        }
        for oneof in real_oneofs {
            let sum = oneof
                .iter()
                .map(|field| format!("usize::from(self.{}.is_some())", field.name()))
                .collect::<Vec<_>>()
                .join(" + ");
            let names = oneof
                .iter()
                .map(FieldDescriptor::name)
                .collect::<Vec<_>>()
                .join(", ");
            output.push_str(&format!(
                "        if {sum} != 1 {{ return Err(ValidationError(\"exactly one of {names} must be present\".to_owned())); }}\n"
            ));
        }
        if descriptor.name() == "OutputInfo" {
            output.push_str(
                "        if self.retained_bytes > self.produced_bytes { return Err(ValidationError(\"retained_bytes cannot exceed produced_bytes\".to_owned())); }\n\
                 \x20       if self.content_complete && self.retained_bytes != self.produced_bytes { return Err(ValidationError(\"complete output must retain every produced byte\".to_owned())); }\n",
            );
        }
        if descriptor.name() == "EIPLimits" {
            output.push_str(
                "        if self.max_output_preview_bytes > self.max_output_bytes_per_stream { return Err(ValidationError(\"max_output_preview_bytes cannot exceed max_output_bytes_per_stream\".to_owned())); }\n",
            );
        }
        if descriptor.name() == "EIPError" {
            output.push_str(
                "        if self.code != self.data.error_type.code() { return Err(ValidationError(\"JSON-RPC code does not match error_type\".to_owned())); }\n",
            );
        }
    }
    output.push_str("        Ok(())\n    }\n}\n\n");
    Ok(())
}

fn render_field_checks(
    output: &mut String,
    field: &FieldDescriptor,
    expression: &str,
    optional: bool,
    extensions: &Extensions,
    indent: usize,
) -> Result<(), String> {
    let padding = " ".repeat(indent);
    let (target, check_indent) = if optional {
        output.push_str(&format!("{padding}if let Some(value) = &{expression} {{\n"));
        ("value".to_owned(), indent + 4)
    } else {
        (expression.to_owned(), indent)
    };
    let check_padding = " ".repeat(check_indent);

    if field.is_list() {
        if let Kind::Message(item) = field.kind()
            && item.package_name() == PACKAGE
        {
            output.push_str(&format!(
                "{check_padding}for value in &{expression} {{ value.validate()?; }}\n"
            ));
        }
    } else if field.is_map() {
        if let Kind::Message(entry) = field.kind()
            && let Kind::Message(value_type) = entry.map_entry_value_field().kind()
            && value_type.package_name() == PACKAGE
        {
            output.push_str(&format!(
                "{check_padding}for value in {expression}.values() {{ value.validate()?; }}\n"
            ));
        }
    } else if let Kind::Message(item) = field.kind()
        && item.package_name() == PACKAGE
    {
        output.push_str(&format!("{check_padding}{target}.validate()?;\n"));
    }

    let options = field.options();
    if options.has_extension(&extensions.field) {
        let option = extension_message(options, &extensions.field)?;
        if option.has_field_by_name("min_length") {
            let minimum = option
                .get_field_by_name("min_length")
                .and_then(|value| value.as_u32())
                .ok_or_else(|| "min_length is not uint32".to_owned())?;
            let measured_length = if matches!(field.kind(), Kind::String) {
                format!("{target}.chars().count()")
            } else {
                format!("{target}.len()")
            };
            let too_short = if minimum == 1 {
                format!("{target}.is_empty()")
            } else {
                format!("{measured_length} < {minimum}usize")
            };
            output.push_str(&format!(
                "{check_padding}if {too_short} {{ return Err(ValidationError(\"{} is shorter than its minimum length\".to_owned())); }}\n",
                field.name()
            ));
        }
        if option.has_field_by_name("max_length") {
            let maximum = option
                .get_field_by_name("max_length")
                .and_then(|value| value.as_u32())
                .ok_or_else(|| "max_length is not uint32".to_owned())?;
            let measured_length = if matches!(field.kind(), Kind::String) {
                format!("{target}.chars().count()")
            } else {
                format!("{target}.len()")
            };
            output.push_str(&format!(
                "{check_padding}if {measured_length} > {maximum}usize {{ return Err(ValidationError(\"{} exceeds its maximum length\".to_owned())); }}\n",
                field.name()
            ));
        }
        if option.has_field_by_name("const_string") {
            let expected = string_field(&option, "const_string")?;
            output.push_str(&format!(
                "{check_padding}if {target}.as_str() != \"{expected}\" {{ return Err(ValidationError(\"{} must equal {expected}\".to_owned())); }}\n",
                field.name()
            ));
        }
        if option.has_field_by_name("const_bool") {
            let expected = option
                .get_field_by_name("const_bool")
                .and_then(|value| value.as_bool())
                .ok_or_else(|| "const_bool is not bool".to_owned())?;
            let bool_target = if optional {
                format!("*{target}")
            } else {
                target.clone()
            };
            let invalid = if expected {
                format!("!{bool_target}")
            } else {
                bool_target
            };
            output.push_str(&format!(
                "{check_padding}if {invalid} {{ return Err(ValidationError(\"{} has an invalid constant value\".to_owned())); }}\n",
                field.name()
            ));
        }
        let format_name = enum_field_name(&option, "string_format")?;
        let string_target = if optional {
            target.clone()
        } else {
            format!("&{target}")
        };
        let invalid = match format_name.as_str() {
            "EIP_STRING_FORMAT_IDENTIFIER" => Some(format!("{target}.is_empty()")),
            "EIP_STRING_FORMAT_PATH" => Some(format!("!validate_absolute_path({string_target})")),
            "EIP_STRING_FORMAT_BASE64_UNPADDED" => {
                Some(format!("!validate_base64_unpadded({string_target})"))
            }
            "EIP_STRING_FORMAT_PROTOCOL_VERSION" => {
                Some(format!("!validate_protocol_version({string_target})"))
            }
            "EIP_STRING_FORMAT_SHA256" => Some(format!("!validate_sha256({string_target})")),
            _ => None,
        };
        if let Some(invalid) = invalid {
            output.push_str(&format!(
                "{check_padding}if {invalid} {{ return Err(ValidationError(\"{} has an invalid EIP string format\".to_owned())); }}\n",
                field.name()
            ));
        }
        let numeric_target = || {
            let dereferenced = if optional {
                format!("*{target}")
            } else {
                target.clone()
            };
            if matches!(field.kind(), Kind::Uint64 | Kind::Fixed64) {
                dereferenced
            } else {
                format!("({dereferenced} as u64)")
            }
        };
        if option.has_field_by_name("minimum") {
            let minimum = option
                .get_field_by_name("minimum")
                .and_then(|value| value.as_u64())
                .ok_or_else(|| "minimum is not uint64".to_owned())?;
            let numeric_target = numeric_target();
            output.push_str(&format!(
                "{check_padding}if {numeric_target} < {minimum} {{ return Err(ValidationError(\"{} is below its minimum\".to_owned())); }}\n",
                field.name()
            ));
        }
        if option.has_field_by_name("maximum") {
            let maximum = option
                .get_field_by_name("maximum")
                .and_then(|value| value.as_u64())
                .ok_or_else(|| "maximum is not uint64".to_owned())?;
            let numeric_target = numeric_target();
            output.push_str(&format!(
                "{check_padding}if {numeric_target} > {maximum} {{ return Err(ValidationError(\"{} exceeds its maximum\".to_owned())); }}\n",
                field.name()
            ));
        }
    }
    if optional {
        output.push_str(&format!("{padding}}}\n"));
    }
    Ok(())
}

fn rust_field_type(field: &FieldDescriptor, optional: bool) -> Result<(String, bool), String> {
    if field.is_map() {
        let Kind::Message(entry) = field.kind() else {
            return Err(format!(
                "map field {} has non-message kind",
                field.full_name()
            ));
        };
        let key = rust_base_type(&entry.map_entry_key_field())?;
        let value = rust_base_type(&entry.map_entry_value_field())?;
        return Ok((format!("BTreeMap<{key}, {value}>"), true));
    }
    let base = rust_base_type(field)?;
    if field.is_list() {
        return Ok((format!("Vec<{base}>"), true));
    }
    if optional {
        return Ok((format!("Option<{base}>"), false));
    }
    Ok((base, false))
}

fn rust_base_type(field: &FieldDescriptor) -> Result<String, String> {
    Ok(match field.kind() {
        Kind::Bool => "bool".to_owned(),
        Kind::Int32 | Kind::Sint32 | Kind::Sfixed32 => "i32".to_owned(),
        Kind::Int64 | Kind::Sint64 | Kind::Sfixed64 => "i64".to_owned(),
        Kind::Uint32 | Kind::Fixed32 => "u32".to_owned(),
        Kind::Uint64 | Kind::Fixed64 => "u64".to_owned(),
        Kind::Float => "f32".to_owned(),
        Kind::Double => "f64".to_owned(),
        Kind::String => "String".to_owned(),
        Kind::Bytes => "Vec<u8>".to_owned(),
        Kind::Enum(item) => item.name().to_owned(),
        Kind::Message(item) if item.full_name() == "google.protobuf.Timestamp" => {
            "chrono::DateTime<chrono::Utc>".to_owned()
        }
        Kind::Message(item) if item.full_name() == "google.protobuf.Value" => {
            "serde_json::Value".to_owned()
        }
        Kind::Message(item) => item.name().to_owned(),
    })
}

fn method_records(
    pool: &DescriptorPool,
    extensions: &Extensions,
) -> Result<Vec<MethodRecord>, String> {
    let service = pool
        .get_service_by_name(&format!("{PACKAGE}.EnvironmentInteractionProtocol"))
        .ok_or_else(|| "descriptor is missing EnvironmentInteractionProtocol service".to_owned())?;
    let records = service
        .methods()
        .map(|method| method_record(&method, extensions))
        .collect::<Result<Vec<_>, _>>()?;
    let mut names = BTreeSet::new();
    for record in &records {
        if record.jsonrpc_method.is_empty() {
            return Err(format!(
                "EIP method {} has no JSON-RPC name",
                record.rpc_name
            ));
        }
        if !names.insert(record.jsonrpc_method.as_str()) {
            return Err(format!(
                "duplicate EIP JSON-RPC method: {}",
                record.jsonrpc_method
            ));
        }
        if record.kind != "request_response" {
            return Err(format!(
                "EIP 1.0 method {} must use correlated request-response",
                record.jsonrpc_method
            ));
        }
        if record.replay_class == "unspecified" || record.error_family == "unspecified" {
            return Err(format!(
                "EIP method {} has incomplete method options",
                record.jsonrpc_method
            ));
        }
        if record.introduced.starts_with("0.") {
            return Err(format!(
                "EIP method {} has invalid introduced version",
                record.jsonrpc_method
            ));
        }
        if record.result_type.is_none() {
            return Err(format!(
                "EIP request-response method {} must have a result message",
                record.jsonrpc_method
            ));
        }
        if record.transfer_action.is_some() != record.transfer_direction.is_some() {
            return Err(format!(
                "EIP method {} has incomplete transfer metadata",
                record.jsonrpc_method
            ));
        }
        if let Some(action) = &record.transfer_action
            && !matches!(action.as_str(), "open" | "close" | "commit" | "abort")
        {
            return Err(format!(
                "EIP method {} has invalid transfer action",
                record.jsonrpc_method
            ));
        }
        if let Some(direction) = &record.transfer_direction
            && !matches!(direction.as_str(), "server_to_client" | "client_to_server")
        {
            return Err(format!(
                "EIP method {} has invalid transfer direction",
                record.jsonrpc_method
            ));
        }
    }
    Ok(records)
}

fn method_record(
    method: &MethodDescriptor,
    extensions: &Extensions,
) -> Result<MethodRecord, String> {
    let option = extension_message(method.options(), &extensions.method)?;
    let kind = enum_field_name(&option, "kind")?
        .trim_start_matches("METHOD_KIND_")
        .to_ascii_lowercase();
    let replay_class = enum_field_name(&option, "replay_class")?
        .trim_start_matches("REPLAY_CLASS_")
        .to_ascii_lowercase();
    let error_family = enum_field_name(&option, "error_family")?
        .trim_start_matches("ERROR_FAMILY_")
        .to_ascii_lowercase();
    let transfer_action_value = enum_field_name(&option, "transfer_action")?
        .trim_start_matches("TRANSFER_ACTION_")
        .to_ascii_lowercase();
    let transfer_direction_value = enum_field_name(&option, "transfer_direction")?
        .trim_start_matches("TRANSFER_DIRECTION_")
        .to_ascii_lowercase();
    let transfer_action = (transfer_action_value != "unspecified").then_some(transfer_action_value);
    let transfer_direction =
        (transfer_direction_value != "unspecified").then_some(transfer_direction_value);
    let major = u32_field(&option, "introduced_major")?;
    let minor = u32_field(&option, "introduced_minor")?;
    let result_type = (method.output().full_name() != "google.protobuf.Empty")
        .then(|| method.output().name().to_owned());
    Ok(MethodRecord {
        rpc_name: method.name().to_owned(),
        rust_name: string_field(&option, "jsonrpc_method")?
            .replace('.', "_")
            .to_snake_case(),
        jsonrpc_method: string_field(&option, "jsonrpc_method")?,
        kind,
        replay_class,
        introduced: format!("{major}.{minor}"),
        error_family,
        transfer_action,
        transfer_direction,
        params_type: method.input().name().to_owned(),
        result_type,
    })
}

fn render_jsonrpc_envelopes(output: &mut String) {
    output.push_str(
        r#"#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum JsonRpcId {
    String(String),
    Integer(i64),
}

fn deserialize_nullable_jsonrpc_id<'de, D>(deserializer: D) -> Result<Option<JsonRpcId>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    Option::<JsonRpcId>::deserialize(deserializer)
}

fn validate_jsonrpc_envelope(
    jsonrpc: &str,
    extensions: &BTreeMap<String, serde_json::Value>,
) -> Result<(), ValidationError> {
    if jsonrpc != "2.0" {
        return Err(ValidationError("jsonrpc must equal 2.0".to_owned()));
    }
    if let Some(name) = extensions.keys().find(|name| name.starts_with("eip_")) {
        return Err(ValidationError(format!("unknown reserved JSON-RPC field: {name}")));
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub id: JsonRpcId,
    pub method: String,
    pub params: BTreeMap<String, serde_json::Value>,
    #[serde(default, flatten, skip_serializing)]
    pub extensions: BTreeMap<String, serde_json::Value>,
}

impl EipValidate for JsonRpcRequest {
    fn validate(&self) -> Result<(), ValidationError> {
        validate_jsonrpc_envelope(&self.jsonrpc, &self.extensions)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JsonRpcSuccessResponse {
    pub jsonrpc: String,
    pub id: JsonRpcId,
    pub result: BTreeMap<String, serde_json::Value>,
    #[serde(default, flatten, skip_serializing)]
    pub extensions: BTreeMap<String, serde_json::Value>,
}

impl EipValidate for JsonRpcSuccessResponse {
    fn validate(&self) -> Result<(), ValidationError> {
        validate_jsonrpc_envelope(&self.jsonrpc, &self.extensions)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JsonRpcErrorResponse {
    pub jsonrpc: String,
    #[serde(deserialize_with = "deserialize_nullable_jsonrpc_id")]
    pub id: Option<JsonRpcId>,
    pub error: EIPError,
    #[serde(default, flatten, skip_serializing)]
    pub extensions: BTreeMap<String, serde_json::Value>,
}

impl EipValidate for JsonRpcErrorResponse {
    fn validate(&self) -> Result<(), ValidationError> {
        validate_jsonrpc_envelope(&self.jsonrpc, &self.extensions)?;
        self.error.validate()
    }
}

"#,
    );
}

fn render_registry(output: &mut String, methods: &[MethodRecord]) {
    output.push_str(
        "#[derive(Debug, Clone, Copy, PartialEq, Eq)]\n\
         pub struct MethodSpec {\n\
         \x20   pub name: &'static str,\n\
         \x20   pub kind: &'static str,\n\
         \x20   pub replay_class: &'static str,\n\
         \x20   pub introduced: &'static str,\n\
         \x20   pub error_family: &'static str,\n\
         \x20   pub transfer_action: Option<&'static str>,\n\
         \x20   pub transfer_direction: Option<&'static str>,\n\
         \x20   pub params_type: &'static str,\n\
         \x20   pub result_type: &'static str,\n\
         }\n\n\
         pub static METHODS: &[MethodSpec] = &[\n",
    );
    for method in methods {
        let result = method
            .result_type
            .as_deref()
            .expect("validated EIP request-response has a result type");
        let transfer_action = method
            .transfer_action
            .as_ref()
            .map_or_else(|| "None".to_owned(), |value| format!("Some(\"{value}\")"));
        let transfer_direction = method
            .transfer_direction
            .as_ref()
            .map_or_else(|| "None".to_owned(), |value| format!("Some(\"{value}\")"));
        output.push_str(&format!(
            "    MethodSpec {{ name: \"{}\", kind: \"{}\", replay_class: \"{}\", introduced: \"{}\", error_family: \"{}\", transfer_action: {}, transfer_direction: {}, params_type: \"{}\", result_type: \"{}\" }},\n",
            method.jsonrpc_method,
            method.kind,
            method.replay_class,
            method.introduced,
            method.error_family,
            transfer_action,
            transfer_direction,
            method.params_type,
            result
        ));
    }
    output.push_str("];\n\n");
}

fn render_dispatch(output: &mut String, methods: &[MethodRecord]) {
    output.push_str("#[derive(Debug, Clone, PartialEq)]\npub enum EipRequest {\n");
    for method in methods {
        output.push_str(&format!(
            "    {}({}),\n",
            method.rpc_name, method.params_type
        ));
    }
    output.push_str("}\n\n#[derive(Debug, Clone, PartialEq, Serialize)]\npub enum EipResponse {\n");
    for method in methods {
        output.push_str(&format!(
            "    {}({}),\n",
            method.rpc_name,
            method
                .result_type
                .as_deref()
                .expect("validated EIP request-response has a result type")
        ));
    }
    output.push_str("}\n\n#[allow(async_fn_in_trait)]\npub trait EipHandler {\n");
    for method in methods {
        output.push_str(&format!(
            "    async fn {}(&self, params: {}) -> Result<{}, EIPError>;\n",
            method.rust_name,
            method.params_type,
            method
                .result_type
                .as_deref()
                .expect("validated EIP request-response has a result type")
        ));
    }
    output.push_str(
        "}\n\n\
         #[derive(Debug)]\n\
         pub struct DispatchSuccess {\n\
         \x20   pub result: serde_json::Value,\n\
         \x20   pub method: &'static str,\n\
         \x20   pub params: serde_json::Value,\n\
         }\n\n\
         #[derive(Debug)]\n\
         pub enum DispatchError {\n\
         \x20   MethodNotFound,\n\
         \x20   InvalidParams(DecodeError),\n\
         \x20   InvalidResult(ValidationError),\n\
         \x20   Encode(serde_json::Error),\n\
         \x20   Method { error: EIPError, method: &'static str, params: serde_json::Value },\n\
         }\n\n\
         pub async fn dispatch<H: EipHandler>(handler: &H, method: &str, params_json: &str) -> Result<DispatchSuccess, DispatchError> {\n\
         \x20   match method {\n",
    );
    for method in methods {
        output.push_str(&format!(
            "        \"{}\" => {{ let params: {} = decode(params_json).map_err(DispatchError::InvalidParams)?; let normalized_params = serde_json::to_value(&params).map_err(DispatchError::Encode)?; let result = handler.{}(params).await.map_err(|error| DispatchError::Method {{ error, method: \"{}\", params: normalized_params.clone() }})?; result.validate().map_err(DispatchError::InvalidResult)?; let result = serde_json::to_value(result).map_err(DispatchError::Encode)?; Ok(DispatchSuccess {{ result, method: \"{}\", params: normalized_params }}) }},\n",
            method.jsonrpc_method,
            method.params_type,
            method.rust_name,
            method.jsonrpc_method,
            method.jsonrpc_method
        ));
    }
    output.push_str("        _ => Err(DispatchError::MethodNotFound),\n    }\n}\n");
}
