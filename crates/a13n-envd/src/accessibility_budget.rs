//! Default-inclusive result accounting shared by the helper and parent Session.
use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::eip::{self, EIPError, EipExpandedJson, ErrorType};

fn expanded<T: DeserializeOwned + EipExpandedJson>(value: &Value) -> Result<Value, EIPError> {
    serde_json::from_value::<T>(value.clone())
        .and_then(|result| result.expanded_json())
        .map_err(|_| {
            super::error(
                ErrorType::InternalError,
                "invalid accessibility result shape",
            )
        })
}

pub(crate) fn result_bytes(
    method: &str,
    value: &Value,
    projection_per_node: usize,
) -> Result<usize, EIPError> {
    let full = match method {
        "computer.a11y_roots" | "computer.a11y_inspect" | "computer.a11y_find" => {
            expanded::<eip::AccessibilityTree>(value)?
        }
        "computer.a11y_read" => expanded::<eip::AccessibilityContent>(value)?,
        "computer.a11y_range" | "computer.a11y_range_read" => {
            expanded::<eip::AccessibilityRangeResult>(value)?
        }
        "computer.a11y_describe" => expanded::<eip::AccessibilityDescription>(value)?,
        _ => {
            return Err(super::error(
                ErrorType::Unsupported,
                "not an accessibility read",
            ));
        }
    };
    if let Some(completeness) = full.get("completeness") {
        let complete = completeness["complete"].as_bool() == Some(true);
        let reason = !completeness["reason"].is_null();
        let continuation = !completeness["continuation"].is_null();
        if (complete && (reason || continuation)) || (!complete && !reason) {
            return Err(super::error(
                ErrorType::InternalError,
                "inconsistent accessibility completeness",
            ));
        }
    }
    let bytes = serde_json::to_vec(&full)
        .map_err(|_| {
            super::error(
                ErrorType::InternalError,
                "cannot encode accessibility result",
            )
        })?
        .len();
    Ok(bytes
        + float_reserve(&full)
        + full["nodes"].as_array().map_or(0, Vec::len) * projection_per_node)
}

// Rust and Python may choose different exponents for the same finite f64.
// 25 bytes covers a sign, 17 significant digits, decimal point and exponent.
fn float_reserve(value: &Value) -> usize {
    match value {
        Value::Number(number) if number.is_f64() => {
            25usize.saturating_sub(number.to_string().len())
        }
        Value::Array(values) => values.iter().map(float_reserve).sum(),
        Value::Object(values) => values.values().map(float_reserve).sum(),
        _ => 0,
    }
}

pub(crate) fn projection_reserve(id: &str) -> Result<usize, EIPError> {
    use eip::AccessibilityImageProjectionStatus;
    let projection = eip::AccessibilityImageProjection {
        source_observation_id: id.to_owned(),
        status: AccessibilityImageProjectionStatus::Unavailable,
        left: Some(-f64::MAX),
        top: Some(-f64::MAX),
        right: Some(-f64::MAX),
        bottom: Some(-f64::MAX),
    }
    .expanded_json()
    .map_err(|_| super::error(ErrorType::InternalError, "cannot encode projection"))?;
    // Each node already includes image_projection:null in its expanded shape.
    Ok(serde_json::to_vec(&projection).unwrap().len() + float_reserve(&projection) - 4)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::eip::EipExpandedJson;
    use serde_json::json;

    #[test]
    fn compact_results_budget_defaults_and_projection_before_paging() {
        let value = json!({
            "backend":"atspi", "sampled_at":"2026-11-09T00:00:00Z",
            "nodes":[{
                "reference":{"backend":"atspi", "backend_epoch":"aep_test", "node_handle":"an_test"},
                "native_role":"frame", "sampled_at":"2026-11-09T00:00:00Z",
                "bounds":{"frame":"atspi_screen", "x":1.5, "y":2.0, "width":3.0, "height":4.0}
            }],
            "completeness":{"complete":true}
        });
        let typed: eip::AccessibilityTree = serde_json::from_value(value.clone()).unwrap();
        let full = typed.expanded_json().unwrap();
        let counted = result_bytes("computer.a11y_inspect", &value, 0).unwrap();
        assert!(counted > serde_json::to_vec(&value).unwrap().len());
        assert!(counted >= serde_json::to_vec(&full).unwrap().len());
        let reserve = projection_reserve("obs_test").unwrap();
        assert_eq!(
            result_bytes("computer.a11y_inspect", &value, reserve).unwrap(),
            counted + reserve
        );
        let mut projected = value;
        projected["nodes"][0]["image_projection"] = json!({
            "source_observation_id":"obs_test", "status":"projected",
            "left":0.0, "top":-f64::MAX, "right":f64::MAX, "bottom":1e-300
        });
        assert!(result_bytes("computer.a11y_inspect", &projected, 0).unwrap() <= counted + reserve);
    }

    #[test]
    fn inconsistent_completeness_is_rejected_at_the_parent_boundary() {
        for completeness in [
            json!({"complete":true,"reason":"node_limit"}),
            json!({"complete":false}),
            json!({"complete":true,"continuation":{"root":{"backend":"atspi","backend_epoch":"aep-test","node_handle":"an-test"},"cursor_handle":"ac-test"}}),
        ] {
            let value = json!({"backend":"atspi","sampled_at":"2026-11-09T00:00:00Z", "nodes":[],"completeness":completeness});
            assert!(result_bytes("computer.a11y_inspect", &value, 0).is_err());
        }
    }

    #[test]
    fn float_reserve_covers_finite_python_number_spellings() {
        for value in [-f64::MAX, f64::MAX, -0.0, 1e-300, 1e20, 1.2345678901234567] {
            let number = json!(value);
            assert!(serde_json::to_vec(&number).unwrap().len() + float_reserve(&number) >= 25);
        }
    }
}
