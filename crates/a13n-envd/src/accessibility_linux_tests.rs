use super::*;
use crate::eip::{
    AccessibilityContent, AccessibilityDescription, AccessibilityRangeResult, AccessibilityTree,
    EipValidate,
};

fn native() -> Native {
    Native {
        epoch: "aep_test".into(),
        connection: None,
        registry: Registry::default(),
    }
}
fn object() -> ObjectRefOwned {
    ObjectRef::new_owned(
        zbus::names::UniqueName::try_from(":1.123").unwrap(),
        zbus::zvariant::ObjectPath::try_from("/org/a11y/atspi/accessible/one").unwrap(),
    )
}

#[test]
fn exact_linux_inventory_excludes_other_platforms_and_unknown_variants() {
    assert_eq!(METHODS.len(), 37);
    assert_eq!(METHODS.iter().collect::<HashSet<_>>().len(), 37);
    assert!(super::super::supports(
        "computer.atspi_document_selections_set"
    ));
    assert!(!super::super::supports("computer.atspi_eval"));
    assert!(!super::super::supports("computer.uia_invoke"));
}
#[test]
fn references_reuse_native_identity_and_reject_other_epochs_and_owners() {
    let mut native = native();
    let reference = native.reference(object()).unwrap();
    assert_eq!(native.reference(object()).unwrap(), reference);
    let range = native.range_ref(&object(), 1, 4).unwrap();
    assert_eq!(native.span(&range).unwrap(), (object(), 1, 4));
    let mut wrong = reference.clone();
    wrong["backend_epoch"] = json!("aep_other");
    assert!(native.node(&wrong).is_err());
    let other = native
        .reference(ObjectRef::new_owned(
            zbus::names::UniqueName::try_from(":1.123").unwrap(),
            zbus::zvariant::ObjectPath::try_from("/other").unwrap(),
        ))
        .unwrap();
    let mut wrong = range.clone();
    wrong["node"] = other;
    assert!(native.span(&wrong).is_err());
}
#[test]
fn registry_eviction_never_occurs_during_a_request() {
    let mut r = Registry::default();
    let first = r.put("an", Handle::Node(object())).unwrap();
    for _ in 1..MAX_HANDLES {
        r.put("an", Handle::Node(object())).unwrap();
    }
    assert!(r.put("an", Handle::Node(object())).is_err());
    assert!(r.get(&first).is_ok());
    r.begin();
    assert_eq!(r.entries.len(), MAX_HANDLES / 2);
}
#[test]
fn text_byte_budget_preserves_utf8_and_coordinates_require_native_integers() {
    let b = Budget::new(&json!({"max_text_bytes":4}));
    assert_eq!(b.text("世😀".into()), "世");
    assert_eq!(b.reason.get(), Some("byte_limit"));
    assert!(point(&json!({"frame":"atspi_screen","x":-120,"y":10})).is_ok());
    assert!(point(&json!({"frame":"atspi_screen","x":1.5,"y":0})).is_err());
    assert!(point(&json!({"frame":"uia_physical_screen","x":1,"y":0})).is_err());
}
#[test]
fn serialized_result_budget_bounds_the_whole_envelope() {
    let reference = native().reference(object()).unwrap();
    let mut tree = json!({"backend":"atspi","sampled_at":now(),"nodes":[{"reference":reference,"native_role":"frame","sampled_at":now(),"name":"x".repeat(2000)}],"completeness":{"complete":true}});
    fit_result(&mut tree, "computer.a11y_roots", 220, 0).unwrap();
    assert!(super::super::budget::result_bytes("computer.a11y_roots", &tree, 0).unwrap() <= 220);
    assert_eq!(tree["completeness"]["reason"], "byte_limit");
    assert!(fit_result(&mut tree, "computer.a11y_roots", 1, 0).is_err());
}
#[test]
fn call_budget_stops_before_another_native_request() {
    let b = Budget::new(&json!({"max_native_calls":1}));
    assert!(b.check().is_ok());
    b.calls.set(1);
    assert!(b.check().is_err());
    assert_eq!(b.reason.get(), Some("call_limit"));
}

fn call(backend: &mut dyn Backend, method: &str, params: Value) -> Value {
    let result = backend
        .execute(method, params, &mut || {
            panic!("read crossed native dispatch")
        })
        .unwrap_or_else(|err| panic!("{method}: {err:?}"));
    match method {
        "computer.a11y_describe" => {
            serde_json::from_value::<AccessibilityDescription>(result.clone())
                .unwrap()
                .validate()
                .unwrap()
        }
        "computer.a11y_roots" | "computer.a11y_find" | "computer.a11y_inspect" => {
            serde_json::from_value::<AccessibilityTree>(result.clone())
                .unwrap()
                .validate()
                .unwrap()
        }
        "computer.a11y_read" => serde_json::from_value::<AccessibilityContent>(result.clone())
            .unwrap()
            .validate()
            .unwrap(),
        _ => serde_json::from_value::<AccessibilityRangeResult>(result.clone())
            .unwrap()
            .validate()
            .unwrap(),
    }
    result
}
fn mutation(backend: &mut dyn Backend, node: &Value, op: &str, request: Value) -> Value {
    let mut count = 0;
    let result = backend
        .execute(
            &format!("computer.atspi_{op}"),
            json!({"node":node,"request":request,"readback":true}),
            &mut || {
                count += 1;
                Ok(())
            },
        )
        .unwrap_or_else(|err| panic!("{op}: {err:?}"));
    assert_eq!(count, 1);
    assert_eq!(result["native_status"], "returned_success");
    let typed: super::super::NativeMutation = serde_json::from_value(result.clone()).unwrap();
    if let Some(observation) = typed.observation {
        observation.validate().unwrap();
    }
    result
}
struct Application(std::process::Child);
impl Drop for Application {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn state(path: &std::path::Path) -> Value {
    serde_json::from_slice(&std::fs::read(path).unwrap_or_default()).unwrap_or(Value::Null)
}
#[track_caller]
fn wait_for(mut predicate: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(15);
    while !predicate() {
        assert!(
            Instant::now() < deadline,
            "fixture readiness deadline expired"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
#[ignore = "requires a disposable Xvfb and D-Bus session; A13N_ATSPI_TEST=1"]
fn linux_accessibility_real_gtk_application() {
    assert_eq!(std::env::var("A13N_ATSPI_TEST").as_deref(), Ok("1"));
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("application.json");
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../dev/fixtures/linux-desktop/accessibility.py");
    let mut app = Application(
        std::process::Command::new("/usr/bin/python3")
            .arg(script)
            .arg(&path)
            .spawn()
            .unwrap(),
    );
    wait_for(|| {
        assert!(app.0.try_wait().unwrap().is_none(), "GTK fixture exited");
        state(&path)["ready"] == true
    });
    let mut backend = new("aep_live_test").unwrap();
    // A mapped GTK window and the lazily activated AT-SPI registry have
    // independent readiness. Observe the backend instead of racing activation.
    wait_for(|| {
        assert!(app.0.try_wait().unwrap().is_none(), "GTK fixture exited");
        call(backend.as_mut(), "computer.a11y_describe", json!({}))["ready"] == true
    });
    let mut application = None;
    wait_for(|| {
        let roots = call(
            backend.as_mut(),
            "computer.a11y_roots",
            json!({"scope":{"kind":"application","process_id":app.0.id()},"limits":{"max_native_calls":4096,"depth":8}}),
        );
        application = roots["nodes"]
            .as_array()
            .and_then(|nodes| nodes.first())
            .map(|n| n["reference"].clone());
        application.is_some()
    });
    let application = application.unwrap();
    let partial = call(
        backend.as_mut(),
        "computer.a11y_roots",
        json!({"scope":{"kind":"applications"},"limits":{"max_native_calls":1}}),
    );
    assert_eq!(partial["completeness"]["reason"], "call_limit");
    let mut tree = json!({"nodes":[]});
    let mut cursor = Value::Null;
    for page_index in 0..256 {
        let page = call(
            backend.as_mut(),
            "computer.a11y_inspect",
            json!({"node":application,"view":"raw","cursor":cursor,"limits":{"max_native_calls":8192,"depth":8,"max_nodes":512}}),
        );
        tree["nodes"]
            .as_array_mut()
            .unwrap()
            .extend(page["nodes"].as_array().unwrap().iter().cloned());
        cursor = page["completeness"]["continuation"].clone();
        if cursor.is_null() {
            assert_eq!(page["completeness"]["complete"], true);
            break;
        }
        assert!(page_index < 255, "baseline traversal did not finish");
    }
    let nodes = tree["nodes"].as_array().unwrap();
    let expected: HashSet<_> = nodes
        .iter()
        .map(|n| n["reference"]["node_handle"].as_str().unwrap().to_owned())
        .collect();
    // Every native child survives both item/node pagination and envelope cuts.
    for limits in [
        json!({"max_nodes":1,"max_items":2,"depth":8,"max_native_calls":512}),
        json!({"max_nodes":512,"max_result_bytes":8000,"depth":8,"max_native_calls":8192}),
    ] {
        let mut cursor = Value::Null;
        let mut observed = HashSet::new();
        let mut finished = false;
        for _ in 0..256 {
            let page = call(
                backend.as_mut(),
                "computer.a11y_inspect",
                json!({"node":application,"view":"raw","limits":limits,"cursor":cursor}),
            );
            assert!(
                serde_json::to_vec(&page).unwrap().len()
                    <= number(&limits, "max_result_bytes", 262144) as usize
            );
            for node in page["nodes"].as_array().unwrap() {
                assert!(
                    observed.insert(
                        node["reference"]["node_handle"]
                            .as_str()
                            .unwrap()
                            .to_owned()
                    ),
                    "repeated page node"
                );
            }
            cursor = page["completeness"]["continuation"].clone();
            if cursor.is_null() {
                finished = true;
                break;
            }
        }
        assert!(finished, "native traversal did not finish");
        assert_eq!(observed, expected);
    }
    let partial = call(
        backend.as_mut(),
        "computer.a11y_inspect",
        json!({"node":application,"view":"raw","limits":{"max_native_calls":1}}),
    );
    assert_eq!(partial["completeness"]["reason"], "call_limit");
    assert!(partial["completeness"]["continuation"].is_object());
    let named = |name: &str| {
        nodes
            .iter()
            .find(|n| n["name"] == name)
            .unwrap_or_else(|| panic!("missing {name}: {tree}"))
            .clone()
    };
    let button = named("Apply changes");
    let action = button["capabilities"]
        .as_array()
        .unwrap()
        .iter()
        .find_map(|c| c.get("action"))
        .unwrap()
        .clone();
    mutation(
        backend.as_mut(),
        &button["reference"],
        "do_action",
        json!({"op":"do_action","action":action}),
    );
    wait_for(|| state(&path)["clicks"] == 1);
    let entry = named("Unicode editor")["reference"].clone();
    mutation(
        backend.as_mut(),
        &entry,
        "text_contents_set",
        json!({"op":"text_contents_set","text":"Hello 世界😀"}),
    );
    wait_for(|| state(&path)["text"] == "Hello 世界😀");
    mutation(
        backend.as_mut(),
        &entry,
        "text_insert",
        json!({"op":"text_insert","position":6,"text":"界é"}),
    );
    wait_for(|| state(&path)["text"] == "Hello 界é世界😀");
    let content = call(
        backend.as_mut(),
        "computer.a11y_read",
        json!({"node":entry,"request":{"kind":"text"}}),
    );
    assert_eq!(content["text"]["text"], "Hello 界é世界😀");
    let range=call(backend.as_mut(),"computer.a11y_range",json!({"node":entry,"query":{"kind":"span","span":{"unit":"atspi_character","start":6,"end":8}}}))["ranges"][0].clone();
    let text = call(
        backend.as_mut(),
        "computer.a11y_range_read",
        json!({"reference":range,"request":{"kind":"text"}}),
    );
    assert_eq!(text["text"]["text"], "界é");
    mutation(
        backend.as_mut(),
        &entry,
        "text_delete",
        json!({"op":"text_delete","range":range}),
    );
    wait_for(|| state(&path)["text"] == "Hello 世界😀");
    let scale = named("Volume")["reference"].clone();
    mutation(
        backend.as_mut(),
        &scale,
        "value_set",
        json!({"op":"value_set","value":71}),
    );
    wait_for(|| state(&path)["value"].as_f64() == Some(71.0));
    let protected = named("Protected editor")["reference"].clone();
    let content = call(
        backend.as_mut(),
        "computer.a11y_read",
        json!({"node":protected,"request":{"kind":"text"}}),
    );
    assert_eq!(content["text"]["status"], "protected");
    assert!(
        !serde_json::to_string(&content)
            .unwrap()
            .contains("fixture-private-value")
    );
    let mut dispatches = 0;
    assert!(backend.execute("computer.atspi_text_contents_set",json!({"node":protected,"request":{"op":"text_contents_set","text":"must not write"}}),&mut||{dispatches+=1;Ok(())}).is_err());
    assert_eq!(dispatches, 0);
    let unsupported = call(
        backend.as_mut(),
        "computer.a11y_read",
        json!({"node":button["reference"],"request":{"kind":"numeric_value"}}),
    );
    assert_eq!(unsupported["field_errors"][0]["reason"], "unsupported");
    mutation(
        backend.as_mut(),
        &entry,
        "text_contents_set",
        json!({"op":"text_contents_set","text":"Straße a a"}),
    );
    for (needle, backward, ignore_case, budget, expected_start, expected_text) in [
        ("a", true, false, 16384, 9, "a"),
        ("a", true, false, 3, 9, "a"),
        ("SS", false, true, 16384, 4, "ß"),
    ] {
        let found = call(
            backend.as_mut(),
            "computer.a11y_range",
            json!({"node":entry,
            "query":{"kind":"find_text","text":needle,"backward":backward,"ignore_case":ignore_case},
            "limits":{"max_matches":1,"max_text_bytes":budget}}),
        );
        assert_eq!(found["ranges"].as_array().unwrap().len(), 1);
        let text = call(
            backend.as_mut(),
            "computer.a11y_range_read",
            json!({"reference":found["ranges"][0],"request":{"kind":"text"}}),
        );
        assert_eq!(text["text"]["span"]["start"], expected_start);
        assert_eq!(text["text"]["text"], expected_text);
    }
    let close = named("Close fixture");
    let action = close["capabilities"]
        .as_array()
        .unwrap()
        .iter()
        .find_map(|c| c.get("action"))
        .unwrap()
        .clone();
    let closed = mutation(
        backend.as_mut(),
        &close["reference"],
        "do_action",
        json!({"op":"do_action","action":action}),
    );
    assert_eq!(closed["effect"], "executed");
    wait_for(|| state(&path)["closed"] == true);
}

#[test]
fn unicode_casefold_query_preserves_unknown_and_observed_false_states() {
    let node = json!({"name":"Straße Σς","states":{"focused":false},"capabilities":[]});
    assert!(matches_query(
        &node,
        &json!({"name":"STRASSE σσ","name_match":"substring_casefold"})
    ));
    assert!(matches_query(&node, &json!({"states":{"focused":false}})));
    assert!(!matches_query(&node, &json!({"states":{"unknown":false}})));
}

#[test]
fn envelope_truncation_keeps_text_status_and_character_span_consistent() {
    let reference = native().reference(object()).unwrap();
    let mut result = json!({"node":reference,"sampled_at":now(),"text":{"status":"text","source":"atspi.Text","text":"界😀a".repeat(100),"span":{"unit":"atspi_character","start":7,"end":307}},"completeness":{"complete":true}});
    fit_result(&mut result, "computer.a11y_read", 620, 0).unwrap();
    let text = result["text"]["text"].as_str().unwrap();
    assert_eq!(result["text"]["span"]["end"], 7 + text.chars().count());
    assert_eq!(
        result["text"]["status"],
        if text.is_empty() { "empty" } else { "text" }
    );
    assert!(super::super::budget::result_bytes("computer.a11y_read", &result, 0).unwrap() <= 620);
    assert!(result.get("nodes").is_none());
}

#[test]
fn registry_accounts_for_payload_bytes_without_evicting_live_operands() {
    let mut r = Registry::default();
    let first = r.put("an", Handle::Node(object())).unwrap();
    let identity = std::sync::Arc::new(vec![(
        "x".repeat(MAX_REGISTRY_BYTES),
        String::new(),
        String::new(),
        String::new(),
    )]);
    assert!(
        r.put(
            "aa",
            Handle::Action {
                node: object(),
                index: 0,
                identity
            }
        )
        .is_err()
    );
    assert!(r.get(&first).is_ok());
    assert_eq!(r.entries.len(), 1);
    assert!(r.bytes < MAX_REGISTRY_BYTES);
}

#[test]
fn item_bounds_apply_to_nested_content_but_not_tree_node_count() {
    let b = Budget::new(&json!({"max_nodes":3,"max_items":1}));
    let node = json!({"children":[1,2],"capabilities":[1,2],"field_errors":[1,2]});
    let mut tree = json!({"nodes":[node.clone(),node.clone(),node]});
    bound_collections(&mut tree, &b, true);
    assert_eq!(tree["nodes"].as_array().unwrap().len(), 3);
    assert_eq!(tree["nodes"][0]["children"].as_array().unwrap().len(), 1);
    assert_eq!(
        tree["nodes"][0]["capabilities"].as_array().unwrap().len(),
        1
    );
    assert_eq!(
        tree["nodes"][0]["field_errors"].as_array().unwrap().len(),
        2
    );
    let mut content =
        json!({"nodes":[{},{}],"properties":[{"value":{"kind":"strings","values":["a","b"]}}]});
    bound_collections(&mut content, &b, false);
    assert_eq!(content["nodes"].as_array().unwrap().len(), 1);
    assert_eq!(
        content["properties"][0]["value"]["values"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(b.reason.get(), Some("item_limit"));
}

#[test]
#[ignore = "requires a disposable D-Bus session; A13N_ATSPI_TEST=1"]
fn linux_accessibility_controlled_native_members() {
    assert_eq!(std::env::var("A13N_ATSPI_TEST").as_deref(), Ok("1"));
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("provider.json");
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../dev/fixtures/linux-desktop/atspi-provider.py");
    let mut app = Application(
        std::process::Command::new("/usr/bin/python3")
            .arg(script)
            .arg(&path)
            .spawn()
            .unwrap(),
    );
    wait_for(|| {
        assert!(
            app.0.try_wait().unwrap().is_none(),
            "native provider exited"
        );
        state(&path)["ready"] == true
    });
    let owner = state(&path)["owner"].as_str().unwrap().to_owned();
    let object = |name: &str| {
        ObjectRef::new_owned(
            zbus::names::UniqueName::try_from(owner.clone()).unwrap(),
            zbus::zvariant::ObjectPath::try_from(format!("/org/a11y/atspi/accessible/{name}"))
                .unwrap(),
        )
    };
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    let mut backend = Linux {
        runtime,
        native: native(),
    };
    backend.runtime.block_on(backend.native.connect()).unwrap();
    let root = backend.native.reference(object("root")).unwrap();
    let child = backend.native.reference(object("child")).unwrap();
    let other = backend.native.reference(object("other")).unwrap();
    let range = backend.native.range_ref(&object("root"), 2, 7).unwrap();
    let foreign_range = backend.native.range_ref(&object("child"), 2, 7).unwrap();
    let identity = backend
        .runtime
        .block_on(
            backend
                .native
                .actions(&object("root"), &Budget::new(&json!({}))),
        )
        .unwrap();
    let handle = backend
        .native
        .registry
        .put(
            "aa",
            Handle::Action {
                node: object("root"),
                index: 0,
                identity,
            },
        )
        .unwrap();
    let action = json!({"node":root,"action_handle":handle,"native_identifier":"activate"});
    let point = json!({"frame":"atspi_screen","x":-12,"y":24});
    let selection =
        json!({"start":child,"start_offset":2,"end":root,"end_offset":7,"start_is_active":true});
    let cases = [
        (
            "do_action",
            json!({"action":action}),
            "Action",
            "DoAction",
            json!([0]),
        ),
        ("focus", json!({}), "Component", "GrabFocus", json!([])),
        (
            "value_set",
            json!({"value":71}),
            "Value",
            "CurrentValue",
            json!([71.0]),
        ),
        (
            "selection_add_child",
            json!({"child":child}),
            "Selection",
            "SelectChild",
            json!([0]),
        ),
        (
            "selection_remove_child",
            json!({"child":child}),
            "Selection",
            "DeselectChild",
            json!([0]),
        ),
        (
            "selection_remove_selected",
            json!({"child":child}),
            "Selection",
            "DeselectSelectedChild",
            json!([0]),
        ),
        (
            "selection_all",
            json!({}),
            "Selection",
            "SelectAll",
            json!([]),
        ),
        (
            "selection_clear",
            json!({}),
            "Selection",
            "ClearSelection",
            json!([]),
        ),
        (
            "text_contents_set",
            json!({"text":"世界😀"}),
            "EditableText",
            "SetTextContents",
            json!(["世界😀"]),
        ),
        (
            "text_insert",
            json!({"position":2,"text":"世界😀"}),
            "EditableText",
            "InsertText",
            json!([2, "世界😀", 10]),
        ),
        (
            "text_delete",
            json!({"range":range}),
            "EditableText",
            "DeleteText",
            json!([2, 7]),
        ),
        (
            "text_copy",
            json!({"range":range}),
            "EditableText",
            "CopyText",
            json!([2, 7]),
        ),
        (
            "text_cut",
            json!({"range":range}),
            "EditableText",
            "CutText",
            json!([2, 7]),
        ),
        (
            "text_paste",
            json!({"position":2}),
            "EditableText",
            "PasteText",
            json!([2]),
        ),
        (
            "caret_set",
            json!({"position":2}),
            "Text",
            "SetCaretOffset",
            json!([2]),
        ),
        (
            "text_selection_add",
            json!({"range":range}),
            "Text",
            "AddSelection",
            json!([2, 7]),
        ),
        (
            "text_selection_set",
            json!({"range":range,"index":0}),
            "Text",
            "SetSelection",
            json!([0, 2, 7]),
        ),
        (
            "text_selection_remove",
            json!({"index":0}),
            "Text",
            "RemoveSelection",
            json!([0]),
        ),
        (
            "document_selections_set",
            json!({"selections":[selection,selection]}),
            "Document",
            "SetTextSelections",
            json!([[
                [
                    (owner.clone(), "/org/a11y/atspi/accessible/child"),
                    2,
                    (owner.clone(), "/org/a11y/atspi/accessible/root"),
                    7,
                    true
                ],
                [
                    (owner.clone(), "/org/a11y/atspi/accessible/child"),
                    2,
                    (owner.clone(), "/org/a11y/atspi/accessible/root"),
                    7,
                    true
                ]
            ]]),
        ),
        (
            "scroll_to",
            json!({"alignment":"anywhere"}),
            "Component",
            "ScrollTo",
            json!([6]),
        ),
        (
            "scroll_to_point",
            json!({"point":point}),
            "Component",
            "ScrollToPoint",
            json!([0, -12, 24]),
        ),
        (
            "range_scroll_to",
            json!({"range":range,"alignment":"anywhere"}),
            "Text",
            "ScrollSubstringTo",
            json!([2, 7, 6]),
        ),
        (
            "range_scroll_to_point",
            json!({"range":range,"point":point}),
            "Text",
            "ScrollSubstringToPoint",
            json!([2, 7, 0, -12, 24]),
        ),
        (
            "component_position_set",
            json!({"point":point}),
            "Component",
            "SetPosition",
            json!([-12, 24, 0]),
        ),
        (
            "component_size_set",
            json!({"width":80,"height":40}),
            "Component",
            "SetSize",
            json!([80, 40]),
        ),
        (
            "component_extents_set",
            json!({"point":point,"width":80,"height":40}),
            "Component",
            "SetExtents",
            json!([-12, 24, 80, 40, 0]),
        ),
        (
            "table_row_select",
            json!({"index":1}),
            "Table",
            "AddRowSelection",
            json!([1]),
        ),
        (
            "table_row_unselect",
            json!({"index":1}),
            "Table",
            "RemoveRowSelection",
            json!([1]),
        ),
        (
            "table_column_select",
            json!({"index":1}),
            "Table",
            "AddColumnSelection",
            json!([1]),
        ),
        (
            "table_column_unselect",
            json!({"index":1}),
            "Table",
            "RemoveColumnSelection",
            json!([1]),
        ),
    ];
    assert_eq!(cases.len(), 30);
    for (index, (op, mut request, interface, member, args)) in cases.into_iter().enumerate() {
        request["op"] = json!(op);
        let mut dispatches = 0;
        let reply = backend
            .execute(
                &format!("computer.atspi_{op}"),
                json!({"node":root,"request":request}),
                &mut || {
                    dispatches += 1;
                    Ok(())
                },
            )
            .unwrap_or_else(|err| panic!("{op}: {err:?}"));
        assert_eq!(dispatches, 1, "{op}");
        assert_eq!(reply["native_status"], "returned_success", "{op}");
        assert_eq!(reply["effect"], "executed", "{op}");
        let calls = state(&path)["calls"].as_array().unwrap().clone();
        assert_eq!(calls.len(), index + 1, "{op}: mutation was replayed");
        assert_eq!(
            calls[index],
            json!(["/org/a11y/atspi/accessible/root", interface, member, args]),
            "{op}"
        );
    }
    let selections = call(
        &mut backend,
        "computer.a11y_read",
        json!({"node":root,"request":{"kind":"document_selections"}}),
    );
    assert_eq!(
        selections["document_selections"],
        json!([selection, selection])
    );
    let partial = call(
        &mut backend,
        "computer.a11y_read",
        json!({"node":root,"request":{"kind":"document_selections"},"limits":{"max_native_calls":14}}),
    );
    assert_eq!(partial["document_selections"], json!([selection]));
    assert_eq!(partial["completeness"]["reason"], "call_limit");
    let partial = call(
        &mut backend,
        "computer.a11y_range",
        json!({"node":root,"query":{"kind":"selection"},"limits":{"max_native_calls":9}}),
    );
    assert_eq!(partial["ranges"].as_array().unwrap().len(), 1);
    assert_eq!(partial["completeness"]["reason"], "call_limit");
    let partial = call(
        &mut backend,
        "computer.a11y_range_read",
        json!({"reference":range,"request":{"kind":"attributes"},"limits":{"max_native_calls":5}}),
    );
    assert_eq!(partial["attribute_runs"].as_array().unwrap().len(), 1);
    assert_eq!(partial["completeness"]["reason"], "call_limit");
    for calls in 1..=3 {
        for (method, params) in [
            (
                "computer.a11y_range",
                json!({"node":root,"query":{"kind":"document"}}),
            ),
            (
                "computer.a11y_range_read",
                json!({"reference":range,"request":{"kind":"text"}}),
            ),
        ] {
            let mut params = params;
            params["limits"] = json!({"max_native_calls":calls});
            let partial = call(&mut backend, method, params);
            assert_eq!(
                partial["completeness"]["reason"], "call_limit",
                "{method}: {calls}"
            );
        }
    }
    // All invalid owners and numeric domains stop before the dispatch boundary.
    for (op, request) in [
        ("text_delete", json!({"range":foreign_range})),
        ("selection_add_child", json!({"child":other})),
        (
            "document_selections_set",
            json!({"selections":[{"start":other,"start_offset":0,"end":child,"end_offset":1}]}),
        ),
        ("value_set", json!({"value":101})),
        ("caret_set", json!({"position":21})),
        ("table_column_select", json!({"index":2})),
        ("component_size_set", json!({"width":0,"height":2})),
    ] {
        let mut dispatches = 0;
        assert!(
            backend
                .execute(
                    &format!("computer.atspi_{op}"),
                    json!({"node":root,"request":request}),
                    &mut || {
                        dispatches += 1;
                        Ok(())
                    }
                )
                .is_err(),
            "{op}"
        );
        assert_eq!(dispatches, 0, "{op}");
    }
    assert_eq!(state(&path)["calls"].as_array().unwrap().len(), 30);
    let configure = |backend: &Linux, mode: &str| {
        backend.runtime.block_on(async {
            let proxy = zbus::Proxy::new(
                backend.native.connection.as_ref().unwrap(),
                owner.as_str(),
                "/org/a11y/atspi/accessible/root",
                "org.a11y.atspi.TestControl",
            )
            .await
            .unwrap();
            proxy.call::<_, _, ()>("Configure", &(mode,)).await.unwrap();
        });
    };
    configure(&backend, "false");
    let reply = backend
        .execute(
            "computer.atspi_focus",
            json!({"node":root,"request":{}}),
            &mut || Ok(()),
        )
        .unwrap();
    assert_eq!(reply["native_status"], "returned_false");
    assert_eq!(reply["effect"], "not_executed");
    configure(&backend, "error");
    let mut dispatches = 0;
    assert!(
        backend
            .execute(
                "computer.atspi_focus",
                json!({"node":root,"request":{}}),
                &mut || {
                    dispatches += 1;
                    Ok(())
                }
            )
            .is_err()
    );
    assert_eq!(dispatches, 1);
    assert_eq!(state(&path)["calls"].as_array().unwrap().len(), 32);
    // Pending native delivery must stay pending; it cannot become a false ack.
    configure(&backend, "no_reply");
    let mut dispatches = 0;
    let pending = backend.runtime.block_on(async {
        tokio::time::timeout(
            Duration::from_millis(150),
            backend.native.execute(
                "computer.atspi_focus",
                json!({"node":root,"request":{}}),
                &mut || {
                    dispatches += 1;
                    Ok(())
                },
            ),
        )
        .await
    });
    assert!(pending.is_err());
    assert_eq!(dispatches, 1);
    assert_eq!(state(&path)["calls"].as_array().unwrap().len(), 33);
}
