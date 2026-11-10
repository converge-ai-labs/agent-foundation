use super::*;

fn reference() -> Value {
    json!({"backend":"uia","backend_epoch":"aep_test","node_handle":"an_test"})
}

#[test]
fn exact_native_inventory_is_closed_and_unique() {
    let unique: std::collections::HashSet<_> = METHODS.iter().collect();
    assert_eq!(unique.len(), 39);
    assert_eq!(METHODS.len(), 39);
    assert_eq!(
        METHODS
            .iter()
            .filter(|m| m.starts_with("computer.uia_"))
            .count(),
        32
    );
    for op in mutation::PATTERNS.iter().flat_map(|(_, _, ops)| *ops) {
        assert!(METHODS.contains(&format!("computer.uia_{op}").as_str()));
    }
    let description = json!({"backend":"uia","backend_epoch":"aep_test","ready":false,"reason":"desktop_unavailable","desktop_identity":"inherited desktop","methods":METHODS,"frames":["uia_physical_screen"],"limits":{}});
    serde_json::from_value::<crate::eip::AccessibilityDescription>(description).unwrap();
}

#[test]
fn native_points_keep_negative_origins_and_reject_other_frames() {
    let point = native_point(&json!({"frame":"uia_physical_screen","x":-1920.4,"y":10.6})).unwrap();
    assert_eq!((point.x, point.y), (-1920, 11));
    assert!(native_point(&json!({"frame":"ax_screen","x":0,"y":0})).is_err());
    assert!(native_point(&json!({"frame":"uia_physical_screen","x":1e20,"y":0})).is_err());
}

#[test]
fn native_queries_casefold_without_inventing_unknown_states() {
    let node = json!({"name":"Straße Σς","states":{"focused":false},"capabilities":[{"operation":"invoke","supported":"yes"}]});
    assert!(matches_query(
        &node,
        &json!({"name":"STRASSE σσ","name_match":"substring_casefold"})
    ));
    assert!(!matches_query(
        &node,
        &json!({"name":"STRASSE","name_match":"exact"})
    ));
    assert!(matches_query(&node, &json!({"states":{"focused":false}})));
    assert!(!matches_query(&node, &json!({"states":{"enabled":false}})));
    assert!(matches_query(&node, &json!({"operation":"invoke"})));
    assert!(!matches_query(&node, &json!({"operation":"toggle"})));
}

#[test]
fn call_budget_preserves_the_last_completed_native_call() {
    let budget = Budget::new(&json!({"limits":{"max_native_calls":1}})).unwrap();
    assert_eq!(budget.call(|| Ok(7)).unwrap(), 7);
    assert!(budget.call(|| Ok(8)).is_err());
    assert_eq!(budget.calls.get(), 1);
    assert_eq!(budget.completeness()["reason"], "call_limit");
}

#[test]
fn utf8_and_result_budgets_preserve_typed_opaque_content() {
    let budget =
        Budget::new(&json!({"limits":{"max_text_bytes":5,"max_result_bytes":1000}})).unwrap();
    assert_eq!(budget.text("中文abc".into()), "中");
    let mut content = json!({"node":reference(),"sampled_at":now(),"completeness":{"complete":true},"text":{"status":"text","source":"UIA.TextPattern","text":"中文🦀".repeat(400)}});
    fit_result(&mut content, "computer.a11y_read", &budget).unwrap();
    assert!(budget.fits("computer.a11y_read", &content).unwrap());
    assert_eq!(content["completeness"]["reason"], "byte_limit");
    assert!(content["text"].get("span").is_none());
    serde_json::from_value::<crate::eip::AccessibilityContent>(content).unwrap();
    let tiny = Budget::new(&json!({"limits":{"max_result_bytes":1}})).unwrap();
    assert_eq!(
        fit_result(&mut tree_result(), "computer.a11y_roots", &tiny)
            .unwrap_err()
            .data
            .error_type,
        ErrorType::QuotaExceeded
    );
}

#[test]
fn paging_reserves_default_fields_and_continuation_before_acceptance() {
    let page = tree_result();
    let without = super::super::budget::result_bytes("computer.a11y_inspect", &page, 0).unwrap();
    let budget = Budget::new(&json!({"limits":{"max_result_bytes":without}})).unwrap();
    assert!(budget.fits("computer.a11y_inspect", &page).unwrap());
    assert!(!page_fits("computer.a11y_inspect", &page, &reference(), &budget).unwrap());
}

#[test]
fn scalar_variants_own_their_native_string_and_boolean() {
    let value = scalar_variant(&json!("中文🦀")).unwrap();
    let raw = unsafe { &value.Anonymous.Anonymous };
    assert_eq!(raw.vt, VT_BSTR);
    assert_eq!(unsafe { raw.Anonymous.bstrVal.to_string() }, "中文🦀");
    let value = scalar_variant(&json!(true)).unwrap();
    let raw = unsafe { &value.Anonymous.Anonymous };
    assert_eq!(raw.vt, VT_BOOL);
    assert_eq!(unsafe { raw.Anonymous.boolVal.0 }, -1);
    assert!(scalar_variant(&json!({"value":1})).is_err());
}

#[test]
fn safe_array_type_checks_and_rectangle_item_boundaries() {
    let array = OwnedArray(unsafe { SafeArrayCreateVector(VT_R8, 0, 8) });
    assert!(!array.0.is_null());
    for i in 0..8i32 {
        let value = f64::from(i);
        unsafe {
            SafeArrayPutElement(array.0, &i, (&value as *const f64).cast()).unwrap();
        }
    }
    let budget = Budget::new(&json!({"limits":{"max_items":1}})).unwrap();
    assert_eq!(array.rectangles(&budget).unwrap(), vec![0.0, 1.0, 2.0, 3.0]);
    assert_eq!(budget.completeness()["reason"], "item_limit");
    let budget = Budget::new(&json!({})).unwrap();
    assert!(array.integers(&budget).is_err());
    let partial_array = OwnedArray(unsafe { SafeArrayCreateVector(VT_R8, 0, 3) });
    assert!(partial_array.rectangles(&budget).is_err());
}

#[test]
fn text_selection_support_prevents_known_unsupported_dispatch() {
    for (selection, op, expected) in [
        (SupportedTextSelection_None, "range_select", false),
        (SupportedTextSelection_None, "range_add_selection", false),
        (SupportedTextSelection_None, "range_remove_selection", false),
        (SupportedTextSelection_Single, "range_select", true),
        (SupportedTextSelection_Single, "range_add_selection", false),
        (
            SupportedTextSelection_Single,
            "range_remove_selection",
            true,
        ),
        (SupportedTextSelection_Multiple, "range_select", true),
        (SupportedTextSelection_Multiple, "range_add_selection", true),
        (
            SupportedTextSelection_Multiple,
            "range_remove_selection",
            true,
        ),
    ] {
        assert_eq!(
            mutation::selection_supported(op, selection),
            expected,
            "{op}"
        );
    }
}

#[test]
#[ignore = "Requires an ordinary signed-in Windows desktop; see the native fixture README"]
fn native_cursor_dependencies_survive_pruning_and_epoch_changes_fail() {
    unsafe { CoInitializeEx(None, COINIT_MULTITHREADED).ok().unwrap() };
    let apartment = Apartment;
    let automation: IUIAutomation =
        unsafe { CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER).unwrap() };
    let element = unsafe { automation.GetRootElement().unwrap() };
    let walker = unsafe { automation.ControlViewWalker().unwrap() };
    let child = unsafe { walker.GetFirstChildElement(&element).unwrap() };
    let child_again = unsafe { walker.GetFirstChildElement(&element).unwrap() };
    assert!(unsafe {
        automation
            .CompareElements(&child, &child_again)
            .unwrap()
            .as_bool()
    });
    let mut native = Native {
        epoch: "aep_registry".into(),
        handles: HashMap::new(),
        identities: HashMap::new(),
        next: 0,
        automation,
        _apartment: apartment,
        _dpi: crate::computer::windows::Dpi::enter().unwrap(),
    };
    let budget = Budget::new(&json!({})).unwrap();
    let root = native.reference(element.clone(), &budget).unwrap();
    let parent = native.reference(element.clone(), &budget).unwrap();
    assert_eq!(root, parent);
    let child_ref = native.reference(child, &budget).unwrap();
    assert_eq!(child_ref, native.reference(child_again, &budget).unwrap());
    let before_sample = native.handles.len();
    let sample = native.sample_fields(&element, &budget).unwrap();
    assert!(!matches_query(
        &sample,
        &json!({"name":"not-a-desktop-name"})
    ));
    assert_eq!(native.handles.len(), before_sample);
    let root_id = string(&root, "node_handle").unwrap().to_owned();
    native.handles.get_mut(&root_id).unwrap().touched = Instant::now() - Duration::from_secs(121);
    let cursor = native
        .put(
            "ac",
            Handle::Cursor {
                root: root.clone(),
                query: Value::Null,
                view: "raw".into(),
                depth: 2,
                pending: vec![],
            },
        )
        .unwrap();
    native.prune(&json!({}));
    assert!(native.handles.contains_key(&root_id));
    // Force registry pressure without depending on thousands of live windows.
    // The oldest ordinary node is evicted while an executing operand and the
    // retained cursor root remain pinned, even when older than the TTL.
    let oldest = native.put("an", Handle::Node(element.clone())).unwrap();
    native.handles.get_mut(&oldest).unwrap().touched = Instant::now() - Duration::from_secs(60);
    let operand = native.put("an", Handle::Node(element.clone())).unwrap();
    native.handles.get_mut(&operand).unwrap().touched = Instant::now() - Duration::from_secs(121);
    while native.handles.len() < 4095 {
        native.put("an", Handle::Node(element.clone())).unwrap();
    }
    native.prune(&json!({"node":{"node_handle":operand}}));
    assert!(native.handles.len() < 2048);
    assert!(!native.handles.contains_key(&oldest));
    assert!(native.handles.contains_key(&operand));
    assert!(native.handles.contains_key(&root_id));
    assert!(native.handles.contains_key(&cursor));
    assert!(native.put("an", Handle::Node(element)).is_ok());
    let mut wrong_epoch = root.clone();
    wrong_epoch["backend_epoch"] = json!("aep_another");
    assert!(native.node(&wrong_epoch).is_err());
    native.handles.get_mut(&cursor).unwrap().touched = Instant::now() - Duration::from_secs(121);
    native.prune(&json!({}));
    assert!(!native.handles.contains_key(&root_id));
    assert!(!native.handles.contains_key(&cursor));
}

#[test]
fn native_exhaustion_overrides_prior_content_truncation() {
    for reason in ["byte_limit", "item_limit"] {
        let b = Budget::new(&json!({"limits":{"max_native_calls":1}})).unwrap();
        b.mark(reason);
        b.call(|| Ok(())).unwrap();
        assert!(
            b.call::<()>(|| panic!("exhausted native closure ran"))
                .is_err()
        );
        assert_eq!(b.reason.get(), Some("call_limit"));
        let mut b = Budget::new(&json!({})).unwrap();
        b.mark(reason);
        b.deadline = Instant::now();
        assert!(
            b.call::<()>(|| panic!("expired native closure ran"))
                .is_err()
        );
        assert_eq!(b.reason.get(), Some("deadline"));
    }
}

#[test]
fn native_budget_consumes_remaining_parent_time() {
    let b = Budget::new(&json!({"_timeout_ms":1,"limits":{"timeout_ms":5000}})).unwrap();
    assert!(b.deadline.saturating_duration_since(Instant::now()) <= Duration::from_millis(1));
    let b = Budget::new(&json!({"_timeout_ms":5000,"limits":{"timeout_ms":1}})).unwrap();
    assert!(b.deadline.saturating_duration_since(Instant::now()) <= Duration::from_millis(1));
}

#[test]
fn direct_table_and_selection_metadata_obey_item_limits() {
    let b = Budget::new(&json!({"limits":{"max_items":1}})).unwrap();
    let mut result = json!({"properties":[]});
    read::property(&mut result, "rows", "GridPattern", json!(3), &b);
    read::property(&mut result, "columns", "GridPattern", json!(4), &b);
    read::property(&mut result, "item_count", "SelectionPattern2", json!(2), &b);
    assert_eq!(result["properties"].as_array().unwrap().len(), 1);
    assert_eq!(result["properties"][0]["name"], "rows");
    assert_eq!(b.reason.get(), Some("item_limit"));
}

#[test]
fn absent_selection_arrays_and_dead_elements_are_not_provider_failures() {
    let b = Budget::new(&json!({})).unwrap();
    let null = native_error(windows::core::Error::from_hresult(windows::core::HRESULT(
        0x80004003u32 as i32,
    )));
    assert!(optional::<()>(Err(null)).unwrap().is_none());
    let dead = native_error(windows::core::Error::from_hresult(windows::core::HRESULT(
        UIA_E_ELEMENTNOTAVAILABLE as i32,
    )));
    assert!(element_unavailable(&dead));
    assert!(
        traversal_optional::<()>(Err(dead.clone()), &b)
            .unwrap()
            .is_none()
    );
    // Sibling acquisition uses ordinary optional: a dead anchor must fail,
    // never masquerade as an observed end of enumeration.
    assert!(optional::<()>(Err(dead)).is_err());
    // Provider errors and quota failures must still propagate, not vanish.
    for kind in [
        ErrorType::ProviderUnavailable,
        ErrorType::Denied,
        ErrorType::QuotaExceeded,
        ErrorType::Timeout,
    ] {
        assert!(traversal_optional::<()>(Err(error(kind, "failure")), &b).is_err());
    }
}

#[test]
fn runtime_identity_is_not_a_truncated_content_collection() {
    let array = OwnedArray(unsafe { SafeArrayCreateVector(VT_I4, 0, 4) });
    for index in 0..4i32 {
        unsafe {
            SafeArrayPutElement(array.0, &index, (&index as *const i32).cast()).unwrap();
        }
    }
    let b = Budget::new(&json!({"limits":{"max_items":1}})).unwrap();
    assert_eq!(array.runtime_id(&b).unwrap(), vec![0, 1, 2, 3]);
    assert!(b.reason.get().is_none());
}
