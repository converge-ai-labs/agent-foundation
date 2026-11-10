use super::*;
fn reference() -> Value {
    json!({"backend":"ax","backend_epoch":"aep_test","node_handle":"an_test"})
}
#[test]
fn exact_native_inventory_is_closed_and_unique() {
    assert_eq!(METHODS.len(), 26);
    assert_eq!(METHODS.iter().collect::<HashSet<_>>().len(), 26);
    assert_eq!(
        METHODS
            .iter()
            .filter(|m| m.starts_with("computer.ax_"))
            .count(),
        19
    );
    for (op, _) in mutation::SETTERS {
        assert!(METHODS.contains(&format!("computer.ax_{op}").as_str()));
    }
    serde_json::from_value::<crate::eip::AccessibilityDescription>(json!({"backend":"ax","ready":false,"reason":"permission_required","desktop_identity":"inherited login session","methods":METHODS,"frames":["ax_screen"],"limits":{}})).unwrap();
}
#[test]
fn native_ranges_preserve_utf16_positions_and_check_overflow() {
    assert_eq!("a😀z".encode_utf16().count(), 4);
    range::validate_span(
        CFRange {
            location: 1,
            length: 2,
        },
        4,
    )
    .unwrap();
    for s in [
        CFRange {
            location: -1,
            length: 1,
        },
        CFRange {
            location: 0,
            length: -1,
        },
        CFRange {
            location: 3,
            length: 2,
        },
        CFRange {
            location: isize::MAX,
            length: 1,
        },
    ] {
        assert!(range::validate_span(s, 4).is_err());
    }
}
#[test]
fn heterogeneous_cf_values_are_checked_and_ranges_are_owned() {
    let b = Budget::new(&json!({})).unwrap();
    let s = CFString::from_str("a😀z");
    assert_eq!(native::text(&s, &b).unwrap(), ("a😀z".into(), 4));
    assert!(native::span(&s).is_err());
    let span = CFRange {
        location: 1,
        length: 2,
    };
    let value = native::span_value(span).unwrap();
    assert_eq!(native::span(&value).unwrap(), span);
    assert!(native::point(&value).is_err());
}
#[test]
fn bounded_text_stops_at_a_scalar_and_keeps_native_units() {
    let b = Budget::new(&json!({"limits":{"max_text_bytes":4}})).unwrap();
    let s = CFString::from_str("a😀z");
    assert_eq!(native::text(&s, &b).unwrap(), ("a".into(), 1));
    assert_eq!(b.reason.get(), Some("byte_limit"));
}
#[test]
fn protocol_identity_and_cf_array_types_do_not_depend_on_text_limits() {
    let b = Budget::new(&json!({"limits":{"max_text_bytes":1}})).unwrap();
    let subrole = CFString::from_str("AXSecureTextField");
    assert_eq!(native::text(&subrole, &b).unwrap().0, "A");
    assert!(native::string_is(&subrole, "AXSecureTextField").unwrap());
    assert!(!native::string_is(&subrole, "AXTextField").unwrap());
    assert!(native::array(&subrole).is_err());
    let array = objc2_core_foundation::CFArray::from_retained_objects(&[subrole]);
    let erased: CFRetained<CFType> = unsafe { CFRetained::cast_unchecked(array) };
    let restored = native::array(&erased).unwrap();
    assert!(native::string_is(&restored.get(0).unwrap(), "AXSecureTextField").unwrap());
}
#[test]
fn byte_fit_updates_text_span_and_status() {
    let mut result = json!({"node":reference(),"sampled_at":now(),"completeness":{"complete":true},"text":{"status":"text","source":"AXStringForRange","text":"a😀z".repeat(1000),"span":{"unit":"ax_native","start":7,"end":4007}}});
    let b = Budget::new(&json!({"limits":{"max_result_bytes":1500}})).unwrap();
    fit_result(&mut result, "computer.a11y_read", &b).unwrap();
    assert!(b.fits("computer.a11y_read", &result).unwrap());
    assert_eq!(
        result["text"]["span"]["end"].as_u64().unwrap(),
        7 + result["text"]["text"]
            .as_str()
            .unwrap()
            .encode_utf16()
            .count() as u64
    );
}
#[test]
fn traversal_reserve_uses_the_typed_wire_envelope() {
    let b = Budget::new(&json!({})).unwrap();
    assert!(page_fits("computer.a11y_inspect", &tree_result(), &reference(), &b).unwrap());
    let b = Budget::new(&json!({"limits":{"max_result_bytes":1}})).unwrap();
    assert!(!page_fits("computer.a11y_inspect", &tree_result(), &reference(), &b).unwrap());
}
#[test]
fn projected_pages_use_the_actual_private_helper_reserve() {
    let mut result = tree_result();
    result["nodes"] = json!([{"reference":reference(),"native_role":"AXTextArea","sampled_at":now(),"name":"editor"}]);
    let p = json!({"limits":{"max_result_bytes":4096},"_projection_bytes":8192});
    let projected = Budget::new(&p).unwrap();
    assert_eq!(projected.projection, 8192);
    assert!(!page_fits("computer.a11y_inspect", &result, &reference(), &projected).unwrap());
    let plain = Budget::new(&json!({"limits":{"max_result_bytes":4096}})).unwrap();
    assert!(page_fits("computer.a11y_inspect", &result, &reference(), &plain).unwrap());
}
#[test]
fn value_capabilities_follow_the_observed_native_type() {
    let text = CFString::from_str("text");
    let number = CFNumber::new_f64(42.0);
    assert!(mutation::value_type_matches(&text, "value_string_set"));
    assert!(!mutation::value_type_matches(&text, "value_number_set"));
    assert!(mutation::value_type_matches(&number, "value_number_set"));
    assert!(!mutation::value_type_matches(&number, "value_string_set"));
    assert!(!mutation::value_type_matches(
        objc2_core_foundation::CFBoolean::new(true),
        "value_number_set"
    ));
}
#[test]
fn errors_keep_native_identity_and_cannot_complete_is_not_timeout() {
    let e = native::status(AXError::CannotComplete).unwrap_err();
    assert_eq!(e.data.error_type, ErrorType::ProviderUnavailable);
    assert_eq!(native::code(&e), Some(-25204));
    assert_eq!(
        native::status(AXError::APIDisabled)
            .unwrap_err()
            .data
            .error_type,
        ErrorType::Denied
    );
    let mut out = json!({"field_errors":[]});
    field_error(
        &mut out,
        "role",
        &native::status(AXError::InvalidUIElement).unwrap_err(),
    );
    assert_eq!(out["field_errors"][0]["reason"], "stale");
}
#[test]
fn query_casefold_and_coordinate_frames_are_explicit() {
    let n = json!({"name":"Straße","states":{"selected":false},"capabilities":[]});
    assert!(matches_query(
        &n,
        &json!({"name":"STRASSE","name_match":"substring_casefold"})
    ));
    assert!(!matches_query(&n, &json!({"states":{"focused":false}})));
    assert!(native_point(&json!({"frame":"uia_physical_screen","x":0,"y":0})).is_err());
    assert!(
        rect_value(CGRect {
            origin: CGPoint { x: -10.0, y: 20.0 },
            size: CGSize {
                width: 30.0,
                height: 40.0
            }
        })
        .is_ok()
    );
}
#[test]
fn backward_text_keeps_the_endpoint_and_native_start() {
    for (text, bytes, suffix, skipped) in [
        ("éabc", 4, "abc", 1),
        ("éabcc", 4, "abcc", 1),
        ("a😀z", 4, "z", 3),
        ("😀😀", 4, "😀", 2),
        ("é", 1, "", 1),
    ] {
        let b = Budget::new(&json!({"limits":{"max_text_bytes":bytes}})).unwrap();
        assert_eq!(
            native::text_suffix(&CFString::from_str(text), &b).unwrap(),
            (suffix.into(), skipped)
        );
        assert_eq!(b.reason.get(), Some("byte_limit"));
    }
}
#[test]
fn selection_fallback_preserves_unsupported_and_native_empty() {
    let unsupported = || native::status(AXError::AttributeUnsupported).unwrap_err();
    assert_eq!(
        range::selection_source::<u8>(Err(unsupported()), || Err(unsupported()))
            .unwrap_err()
            .data
            .error_type,
        ErrorType::Unsupported
    );
    assert_eq!(
        range::selection_source(Err(unsupported()), || Ok(7)).unwrap(),
        Some((false, 7))
    );
    assert_eq!(
        range::selection_source::<u8>(
            Err(native::status(AXError::NoValue).unwrap_err()),
            || panic!("native empty must not fall back")
        )
        .unwrap(),
        None
    );
    assert_eq!(
        range::selection_source::<u8>(Err(unsupported()), || Err(
            native::status(AXError::NoValue).unwrap_err()
        ))
        .unwrap(),
        None
    );
    assert_eq!(
        range::selection_source(Ok(0), || panic!("plural success must not fall back")).unwrap(),
        Some((true, 0))
    );
    assert!(
        range::selection_source::<u8>(
            Err(native::status(AXError::CannotComplete).unwrap_err()),
            || panic!("provider failure must not fall back")
        )
        .is_err()
    );
}
#[test]
fn small_native_budgets_fail_without_a_nonprogressing_cursor() {
    let b = Budget::new(&json!({"limits":{"max_native_calls":1}})).unwrap();
    b.mark("byte_limit");
    b.calls.set(1);
    assert_eq!(
        ensure_progress(0, &b).unwrap_err().data.error_type,
        ErrorType::Timeout
    );
    assert_eq!(b.reason.get(), Some("call_limit"));
    let b = Budget::new(&json!({"_timeout_ms":0})).unwrap();
    b.mark("item_limit");
    assert!(ensure_progress(0, &b).is_err());
    assert_eq!(b.reason.get(), Some("deadline"));
    let b = Budget::new(&json!({"_timeout_ms":100})).unwrap();
    assert!(b.deadline.saturating_duration_since(Instant::now()) <= Duration::from_millis(95));
    b.mark("byte_limit");
    assert_eq!(
        ensure_progress(0, &b).unwrap_err().data.error_type,
        ErrorType::QuotaExceeded
    );
    assert!(ensure_progress(1, &b).is_ok());
    // A prior collection cut must not hide a subsequent oversized first node.
    let b = Budget::new(&json!({"limits":{"max_items":1,"max_result_bytes":128}})).unwrap();
    b.mark("item_limit");
    let root = json!({"backend":"ax","backend_epoch":"aep_test","node_handle":"an_test"});
    let mut out = tree_result();
    out["nodes"] = json!([{"reference":root,"native_role":"AXButton",
        "sampled_at":now(),"name":"x".repeat(256)}]);
    assert!(!page_fits("computer.a11y_inspect", &out, &root, &b).unwrap());
    b.mark("byte_limit");
    assert_eq!(b.reason.get(), Some("item_limit"));
    assert_eq!(
        ensure_progress(0, &b).unwrap_err().data.error_type,
        ErrorType::QuotaExceeded
    );
}
#[test]
fn tree_accepts_the_protocol_node_operand_before_native_work() {
    let mut native = Native {
        epoch: "aep_test".into(),
        next: 0,
        handles: HashMap::new(),
    };
    let n = unsafe { AXUIElement::new_application(std::process::id() as i32) };
    let node = native.reference(n).unwrap();
    for method in ["computer.a11y_inspect", "computer.a11y_find"] {
        let payload = json!({"node":node,"limits":{"max_native_calls":1}});
        let b = Budget::new(&payload).unwrap();
        b.calls.set(1);
        // No AX message or TCC grant is needed: resolving the wire operand
        // must reach the budget check instead of rejecting a missing `root`.
        assert_eq!(
            native
                .tree(method, &payload, &b)
                .unwrap_err()
                .data
                .error_type,
            ErrorType::Timeout
        );
    }
}
#[test]
fn containing_collections_respect_the_item_budget() {
    let b = Budget::new(&json!({"limits":{"max_items":1}})).unwrap();
    let out = json!({"properties":[{}],"nodes":[{}]});
    assert!(!collection_room(&out, "properties", &b));
    assert!(!collection_room(&out, "nodes", &b));
    assert_eq!(b.reason.get(), Some("item_limit"));
}
#[test]
fn registry_eviction_frees_each_kind_and_pins_operands_and_owners() {
    let mut native = Native {
        epoch: "aep_test".into(),
        next: 0,
        handles: HashMap::new(),
    };
    let n = unsafe { AXUIElement::new_application(std::process::id() as i32) };
    let owner = native.reference(n.clone()).unwrap();
    let owner_id = owner["node_handle"].as_str().unwrap().to_owned();
    for i in 0..512 {
        native
            .put(
                "aa",
                Handle::Action {
                    owner: owner_id.clone(),
                    name: CFString::from_str(&format!("Action{i}")),
                },
            )
            .unwrap();
    }
    let action = "aa_2";
    for _ in 0..256 {
        native
            .range_reference(
                &owner,
                CFRange {
                    location: 0,
                    length: 0,
                },
            )
            .unwrap();
    }
    for _ in 0..64 {
        native
            .put(
                "ac",
                Handle::Cursor {
                    root: owner.clone(),
                    query: Value::Null,
                    view: "raw".into(),
                    depth: 2,
                    pending: vec![Pending::Node(n.clone(), 0, Some(owner.clone()))],
                },
            )
            .unwrap();
    }
    native.prune(&json!({"request":{"action":{"action_handle":action}}}));
    assert!(native.handles.contains_key(action));
    assert!(native.handles.contains_key(&owner_id));
    for kind in 0..3 {
        assert!(
            native
                .handles
                .values()
                .filter(|e| handle_kind(&e.value) == kind)
                .count()
                < handle_capacity(kind) / 2
        );
    }
    native
        .range_reference(
            &owner,
            CFRange {
                location: 0,
                length: 0,
            },
        )
        .unwrap();
    for _ in 0..3000 {
        native.put("an", Handle::Node(n.clone())).unwrap();
    }
    native.prune(&json!({"node":owner}));
    assert!(native.handles.len() < 2048);
    assert!(native.handles.contains_key(&owner_id));
}
#[test]
#[ignore = "requires a trusted macOS login session and the AppKit fixture"]
fn native_appkit_read_action_text_and_owner_validation() {
    let pid = std::env::var("A13N_AX_FIXTURE_PID")
        .expect("set fixture PID")
        .parse::<i32>()
        .unwrap();
    let mut backend = new("aep_native").unwrap();
    let mut no_dispatch = || panic!("read dispatched");
    let description = backend
        .execute("computer.a11y_describe", json!({}), &mut no_dispatch)
        .unwrap();
    assert_eq!(description["ready"], true);
    let roots = backend
        .execute(
            "computer.a11y_roots",
            json!({"scope":{"kind":"application","process_id":pid}}),
            &mut no_dispatch,
        )
        .unwrap();
    let root = roots["nodes"][0]["reference"].clone();
    let found=backend.execute("computer.a11y_find",json!({"node":root,"query":{"identifier":"fixture-editor"},"limits":{"depth":8,"max_nodes":512,"max_native_calls":8192}}),&mut no_dispatch).unwrap();
    let node = found["nodes"][0]["reference"].clone();
    assert!(node.is_object());
    let low = backend
        .execute(
            "computer.a11y_inspect",
            json!({"node":root,"limits":{"max_native_calls":1}}),
            &mut no_dispatch,
        )
        .unwrap_err();
    assert_eq!(low.data.error_type, ErrorType::Timeout);
    let mut dispatched = 0;
    let ack = backend
        .execute(
            "computer.ax_value_string_set",
            json!({"node":node,"request":{"op":"value_string_set","value":"updated 😀"},"readback":true}),
            &mut || {
                dispatched += 1;
                Ok(())
            },
        )
        .unwrap();
    assert_eq!(dispatched, 1);
    assert_eq!(ack["native_status"], "returned_success");
    assert!(ack["observation"].is_null());
    let text = backend
        .execute(
            "computer.a11y_read",
            json!({"node":node,"request":{"kind":"text"}}),
            &mut no_dispatch,
        )
        .unwrap();
    assert_eq!(text["text"]["text"], "updated 😀");
}
