mod generated {
    #![allow(clippy::all, unused_variables)]

    include!(concat!(env!("OUT_DIR"), "/eip_wire.rs"));
}

pub use generated::*;

#[cfg(test)]
mod tests {
    use serde::{Serialize, de::DeserializeOwned};

    use super::{
        CommandNetwork, DataFrame, DataFrameKind, DataResetStatus, EIP_DESCRIPTOR_SHA256,
        EIP_PROTO_PACKAGE, EIP_PROTOCOL_VERSION, EIPCallContext, EIPError, EIPLimits,
        EIPServerInfo, EipValidate, EncodedBytes, EnvironmentReadinessParams,
        EnvironmentReadinessResult, ErrorType, FileFindParams, FileSearchMatch, FileSearchParams,
        FileSearchResult, FileStatParams, FileStatResult, InitializeParams, JsonRpcErrorResponse,
        JsonRpcRequest, JsonRpcSuccessResponse, METHODS, OutputInfo, OutputReadParams,
        OutputReference, ProcessWriteStdinParams, ReceiptGetParams, ShellExecParams, decode,
        decode_data_frame, encode, encode_data_frame,
    };

    fn assert_golden<T>(value: serde_json::Value)
    where
        T: DeserializeOwned + Serialize + EipValidate,
    {
        let payload = serde_json::to_string(&value).expect("fixture is JSON");
        let decoded: T = decode(&payload).expect("fixture follows EIP profile");
        let encoded = encode(&decoded).expect("model encodes canonically");
        assert_eq!(
            encoded,
            serde_json::to_vec(&value).expect("fixture encodes")
        );
        assert_eq!(
            serde_json::to_value(decoded).expect("model serializes"),
            value
        );
    }

    #[test]
    fn generated_registry_has_complete_v1_surface() {
        assert_eq!(EIP_PROTOCOL_VERSION, "0.1");
        assert_eq!(EIP_PROTO_PACKAGE, "a13n.agent_envd.eip.v1");
        assert_eq!(METHODS.len(), 35);
        assert!(
            METHODS
                .iter()
                .all(|method| method.kind == "request_response")
        );
        assert_eq!(ErrorType::IntegrityMismatch.code(), -32061);
        assert_eq!(
            METHODS
                .iter()
                .filter(|method| method.replay_class == "active_only")
                .count(),
            19
        );
        assert_eq!(
            METHODS
                .iter()
                .filter(|method| method.replay_class == "terminal_evidence")
                .count(),
            15
        );
        assert_eq!(
            METHODS
                .iter()
                .filter(|method| method.replay_class == "ledger_external")
                .map(|method| method.name)
                .collect::<Vec<_>>(),
            vec!["initialize"]
        );
        let transfer_methods = METHODS
            .iter()
            .filter(|method| method.transfer_action.is_some())
            .collect::<Vec<_>>();
        assert_eq!(transfer_methods.len(), 5);
        assert!(
            transfer_methods
                .iter()
                .all(|method| method.transfer_direction.is_some())
        );
    }

    #[test]
    fn generated_descriptor_digest_matches_checked_descriptor() {
        use sha2::{Digest, Sha256};

        let descriptor = include_bytes!("../../protocol/eip/v1/descriptor.pb");
        assert_eq!(
            EIP_DESCRIPTOR_SHA256,
            format!("{:x}", Sha256::digest(descriptor))
        );
    }

    #[test]
    fn generated_registry_has_unique_jsonrpc_names() {
        let mut names = METHODS.iter().map(|method| method.name).collect::<Vec<_>>();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), METHODS.len());
    }

    #[test]
    fn rust_models_match_shared_golden_values() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../protocol/eip/v1/testdata/golden.json"))
                .expect("golden fixture is valid JSON");
        for case in fixture["cases"].as_array().expect("cases is an array") {
            let value = case["value"].clone();
            match case["type"].as_str().expect("case type is a string") {
                "InitializeParams" => assert_golden::<InitializeParams>(value),
                "EnvironmentReadinessParams" => assert_golden::<EnvironmentReadinessParams>(value),
                "EnvironmentReadinessResult" => assert_golden::<EnvironmentReadinessResult>(value),
                "FileFindParams" => assert_golden::<FileFindParams>(value),
                "FileSearchParams" => assert_golden::<FileSearchParams>(value),
                "FileSearchMatch" => assert_golden::<FileSearchMatch>(value),
                "FileSearchResult" => assert_golden::<FileSearchResult>(value),
                "FileStatParams" => assert_golden::<FileStatParams>(value),
                "FileStatResult" => assert_golden::<FileStatResult>(value),
                "ShellExecParams" => assert_golden::<ShellExecParams>(value),
                "OutputReadParams" => assert_golden::<OutputReadParams>(value),
                "ProcessWriteStdinParams" => assert_golden::<ProcessWriteStdinParams>(value),
                "ReceiptGetParams" => assert_golden::<ReceiptGetParams>(value),
                "EIPError" => assert_golden::<EIPError>(value),
                other => panic!("unhandled golden fixture type: {other}"),
            }
        }
    }

    #[test]
    fn rust_models_apply_and_canonically_omit_explicit_defaults() {
        let value = serde_json::json!({
            "context": {"operation_id": "op-defaults"},
            "request": {
                "command": {
                    "kind": "argv",
                    "executable_spec": {"kind": "name", "name": "true"},
                    "arguments": []
                },
                "cwd": {"mount_id": "workspace", "path": "/repo"},
                "environment": {"set": {}, "unset": []},
                "network": "configured",
                "limits": {},
                "keep_stdin_open": false
            }
        });
        let payload = serde_json::to_string(&value).expect("fixture is JSON");

        let decoded: ShellExecParams = decode(&payload).expect("fixture follows EIP profile");

        assert_eq!(decoded.request.network, CommandNetwork::Configured);
        assert!(decoded.request.environment.set.is_empty());
        assert!(decoded.request.environment.unset.is_empty());
        assert!(decoded.request.limits.wall_time_ms.is_none());
        assert_eq!(
            serde_json::to_value(decoded).expect("model serializes"),
            serde_json::json!({
                "context": {"operation_id": "op-defaults"},
                "request": {
                    "command": {
                        "kind": "argv",
                        "executable_spec": {"kind": "name", "name": "true"}
                    },
                    "cwd": {"mount_id": "workspace", "path": "/repo"}
                }
            })
        );
    }

    fn valid_eip_limits() -> serde_json::Value {
        serde_json::json!({
            "max_request_bytes": 1,
            "max_response_bytes": 1,
            "max_concurrent_operations": 1,
            "max_processes": 1,
            "max_operation_duration_ms": 1,
            "max_output_preview_bytes": 1,
            "max_output_bytes_per_stream": 1,
            "max_transfer_frame_bytes": 25,
            "max_concurrent_file_transfers": 1,
            "max_file_transfer_bytes": 1
        })
    }

    #[test]
    fn rust_eip_limits_define_valid_output_bounds() {
        let limits = valid_eip_limits();
        assert!(decode::<EIPLimits>(&limits.to_string()).is_ok());

        for (field, value) in [
            ("max_request_bytes", 0),
            ("max_output_preview_bytes", 2),
            ("max_transfer_frame_bytes", 24),
            ("max_file_transfer_bytes", 0),
        ] {
            let mut invalid = limits.clone();
            invalid[field] = value.into();
            assert!(decode::<EIPLimits>(&invalid.to_string()).is_err());
        }
    }

    #[test]
    fn rust_jsonrpc_integer_ids_use_signed_64_bit_range() {
        let request_max = r#"{"jsonrpc":"2.0","id":9223372036854775807,"method":"environment.describe","params":{}}"#;
        let request_over = r#"{"jsonrpc":"2.0","id":9223372036854775808,"method":"environment.describe","params":{}}"#;
        assert!(decode::<JsonRpcRequest>(request_max).is_ok());
        assert!(decode::<JsonRpcRequest>(request_over).is_err());

        let success_min = r#"{"jsonrpc":"2.0","id":-9223372036854775808,"result":{}}"#;
        let success_under = r#"{"jsonrpc":"2.0","id":-9223372036854775809,"result":{}}"#;
        assert!(decode::<JsonRpcSuccessResponse>(success_min).is_ok());
        assert!(decode::<JsonRpcSuccessResponse>(success_under).is_err());

        let error_max = r#"{"jsonrpc":"2.0","id":9223372036854775807,"error":{"code":-32603,"message":"internal error","data":{"error_type":"internal_error","retry_hint":"never","dispatch_stage":"unknown"}}}"#;
        let error_over = r#"{"jsonrpc":"2.0","id":9223372036854775808,"error":{"code":-32603,"message":"internal error","data":{"error_type":"internal_error","retry_hint":"never","dispatch_stage":"unknown"}}}"#;
        let error_missing_id = r#"{"jsonrpc":"2.0","error":{"code":-32603,"message":"internal error","data":{"error_type":"internal_error","retry_hint":"never","dispatch_stage":"unknown"}}}"#;
        assert!(decode::<JsonRpcErrorResponse>(error_max).is_ok());
        assert!(decode::<JsonRpcErrorResponse>(error_over).is_err());
        assert!(decode::<JsonRpcErrorResponse>(error_missing_id).is_err());
    }

    #[test]
    fn rust_generated_jsonrpc_envelope_rejects_reserved_extensions() {
        let valid = r#"{"jsonrpc":"2.0","id":"request-1","method":"environment.describe","params":{},"trace_context":"value"}"#;
        let decoded = decode::<JsonRpcRequest>(valid).expect("ordinary extensions are accepted");
        let reencoded = encode(&decoded).expect("envelope encodes");
        assert!(
            !String::from_utf8(reencoded)
                .expect("JSON is UTF-8")
                .contains("trace_context")
        );

        let reserved = r#"{"jsonrpc":"2.0","id":"request-1","method":"environment.describe","params":{},"eip_authority":"unexpected"}"#;
        assert!(decode::<JsonRpcRequest>(reserved).is_err());
    }

    #[test]
    fn rust_error_rejects_removed_retention_fields_and_identity() {
        let removed_identity = r#"{"code":-32022,"message":"gap","data":{"error_type":"retention_gap","retry_hint":"never","dispatch_stage":"completed"}}"#;
        assert!(decode::<EIPError>(removed_identity).is_err());

        let removed_bounds = r#"{"code":-32603,"message":"wrong","data":{"error_type":"internal_error","retry_hint":"never","dispatch_stage":"completed","available_start":0,"available_end":1}}"#;
        assert!(decode::<EIPError>(removed_bounds).is_err());
    }

    #[test]
    fn rust_encoder_rejects_structurally_invalid_models() {
        assert!(
            encode(&EIPServerInfo {
                name: "not-agent-envd".to_owned(),
                version: "1.0.0".to_owned(),
            })
            .is_err()
        );
        assert!(
            encode(&OutputInfo {
                reference: OutputReference("output-1".to_owned()),
                producer_complete: true,
                content_complete: true,
                produced_bytes: 2,
                retained_bytes: 1,
                preview: EncodedBytes {
                    encoding: "base64".to_owned(),
                    data: "YQ".to_owned(),
                },
            })
            .is_err()
        );
    }

    #[test]
    fn rust_operation_ids_use_the_canonical_character_bound() {
        let maximum = "🧪".repeat(128);
        let valid = serde_json::json!({"operation_id": maximum});
        assert!(decode::<EIPCallContext>(&valid.to_string()).is_ok());

        let too_long = serde_json::json!({"operation_id": "🧪".repeat(129)});
        assert!(decode::<EIPCallContext>(&too_long.to_string()).is_err());
    }

    #[test]
    fn rust_decoder_rejects_profile_violations() {
        let zero_generation = r#"{"code":-32603,"message":"invalid generation","data":{"error_type":"internal_error","retry_hint":"never","dispatch_stage":"pre_dispatch","generation":0}}"#;
        assert!(decode::<EIPError>(zero_generation).is_err());

        let unknown = r#"{"context":{"operation_id":"op","principal":"caller"},"path":{"mount_id":"workspace","path":"/repo"}}"#;
        assert!(decode::<FileStatParams>(unknown).is_err());

        let invalid_path = r#"{"context":{"operation_id":"op"},"path":{"mount_id":"workspace","path":"/repo/../secret"}}"#;
        assert!(decode::<FileStatParams>(invalid_path).is_err());

        let missing_reference = r#"{"producer_complete":true,"content_complete":true,"produced_bytes":1,"retained_bytes":1,"preview":{"encoding":"base64","data":"YQ"}}"#;
        assert!(decode::<OutputInfo>(missing_reference).is_err());
        let valid_output = r#"{"reference":"output-1","producer_complete":true,"content_complete":true,"produced_bytes":1,"retained_bytes":1,"preview":{"encoding":"base64","data":"YQ"}}"#;
        assert!(decode::<OutputInfo>(valid_output).is_ok());
        let incomplete_claim = r#"{"reference":"output-1","producer_complete":true,"content_complete":true,"produced_bytes":2,"retained_bytes":1,"preview":{"encoding":"base64","data":"YQ"}}"#;
        assert!(decode::<OutputInfo>(incomplete_claim).is_err());
        let removed_capture_field = r#"{"reference":"output-1","producer_complete":true,"content_complete":true,"produced_bytes":1,"retained_bytes":1,"captured_bytes":1,"preview":{"encoding":"base64","data":"YQ"}}"#;
        assert!(decode::<OutputInfo>(removed_capture_field).is_err());

        let missing_position = r#"{"context":{"operation_id":"op"},"reference":"output-1"}"#;
        assert!(decode::<OutputReadParams>(missing_position).is_err());

        let missing_receipt_selector = r#"{"context":{"operation_id":"op-query"}}"#;
        assert!(decode::<ReceiptGetParams>(missing_receipt_selector).is_err());
        let duplicate_receipt_selector = r#"{"context":{"operation_id":"op-query"},"receipt_ref":"receipt-1","operation_id":"op-target"}"#;
        assert!(decode::<ReceiptGetParams>(duplicate_receipt_selector).is_err());

        let duplicate_map_key = r#"{"context":{"operation_id":"op"},"request":{"command":{"kind":"argv","executable_spec":{"kind":"name","name":"true"}},"cwd":{"mount_id":"workspace","path":"/repo"},"environment":{"set":{"PATH":"one","PATH":"two"}}}}"#;
        assert!(decode::<ShellExecParams>(duplicate_map_key).is_err());
    }

    fn decode_hex(value: &str) -> Vec<u8> {
        assert_eq!(value.len() % 2, 0);
        value
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let high = char::from(pair[0]).to_digit(16).expect("hex digit");
                let low = char::from(pair[1]).to_digit(16).expect("hex digit");
                ((high << 4) | low) as u8
            })
            .collect()
    }

    #[test]
    fn rust_data_frame_codec_matches_shared_golden_frames() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../protocol/eip/v1/testdata/data-frame-golden.json"
        ))
        .expect("data-frame fixture is valid JSON");
        for case in fixture["cases"].as_array().expect("cases is an array") {
            let kind = match case["kind"].as_str().expect("kind is a string") {
                "attach" => DataFrameKind::Attach,
                "attached" => DataFrameKind::Attached,
                "chunk" => DataFrameKind::Chunk,
                "end" => DataFrameKind::End,
                "end_ack" => DataFrameKind::EndAck,
                "reset" => DataFrameKind::Reset,
                other => panic!("unknown fixture kind: {other}"),
            };
            let reset_status = case["reset_status"].as_str().map(|value| match value {
                "source" => DataResetStatus::Source,
                other => panic!("unknown fixture reset status: {other}"),
            });
            let frame = DataFrame {
                kind,
                handle: case["handle"]
                    .as_str()
                    .expect("handle is a string")
                    .to_owned(),
                offset: case["offset"].as_u64().expect("offset is uint64"),
                payload: decode_hex(case["payload_hex"].as_str().expect("payload is hex")),
                reset_status,
            };
            let expected = decode_hex(case["frame_hex"].as_str().expect("frame is hex"));
            assert_eq!(
                encode_data_frame(&frame, 1024).expect("fixture frame encodes"),
                expected
            );
            assert_eq!(
                decode_data_frame(&expected, 1024).expect("fixture frame decodes"),
                frame
            );
        }
    }

    #[test]
    fn rust_data_frame_codec_rejects_structural_violations() {
        let valid = encode_data_frame(
            &DataFrame {
                kind: DataFrameKind::Attach,
                handle: "reader-1".to_owned(),
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            },
            1024,
        )
        .expect("valid frame encodes");

        for (index, value) in [(0, b'X'), (4, 2), (5, 99), (10, 1), (7, 1)] {
            let mut invalid = valid.clone();
            invalid[index] = value;
            assert!(decode_data_frame(&invalid, 1024).is_err());
        }
        assert!(decode_data_frame(&valid[..valid.len() - 1], 1024).is_err());
        let mut trailing = valid.clone();
        trailing.push(0);
        assert!(decode_data_frame(&trailing, 1024).is_err());
        assert!(decode_data_frame(&valid, valid.len() - 1).is_err());

        for frame in [
            DataFrame {
                kind: DataFrameKind::End,
                handle: "reader-1".to_owned(),
                offset: 1,
                payload: vec![1],
                reset_status: None,
            },
            DataFrame {
                kind: DataFrameKind::Reset,
                handle: "reader-1".to_owned(),
                offset: 1,
                payload: Vec::new(),
                reset_status: None,
            },
            DataFrame {
                kind: DataFrameKind::Attach,
                handle: String::new(),
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            },
            DataFrame {
                kind: DataFrameKind::Chunk,
                handle: "reader-1".to_owned(),
                offset: u64::MAX,
                payload: vec![1],
                reset_status: None,
            },
        ] {
            assert!(encode_data_frame(&frame, 1024).is_err());
        }
    }
}
