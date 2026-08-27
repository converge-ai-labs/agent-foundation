from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from a13n_envd_client.eip.v1 import (
    EIP_ERROR_CODES,
    EIP_PROTO_PACKAGE,
    EIP_PROTOCOL_VERSION,
    METHODS,
    DataFrame,
    DataFrameKind,
    DataResetStatus,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    decode_data_frame,
    decode_model,
    encode_data_frame,
    encode_model,
)
from a13n_envd_client.eip.v1.models import (
    CommandEnvironment,
    CommandNetwork,
    ContentDigest,
    EIPCallContext,
    EIPError,
    EIPLimits,
    EncodedBytes,
    ErrorType,
    FileFindParams,
    FileReadCompletion,
    FileStatParams,
    FileStatResult,
    InitializeParams,
    OutputInfo,
    OutputReadParams,
    ProcessWriteStdinParams,
    ReceiptGetParams,
    ShellExecParams,
)
from pydantic import BaseModel, ValidationError

REPOSITORY_ROOT = Path(__file__).parents[4]
GOLDEN_PATH = REPOSITORY_ROOT / "crates/agent-envd/protocol/eip/v1/testdata/golden.json"
DATA_FRAME_GOLDEN_PATH = REPOSITORY_ROOT / "crates/agent-envd/protocol/eip/v1/testdata/data-frame-golden.json"


def valid_eip_limits() -> dict[str, int]:
    return {
        "max_request_bytes": 1,
        "max_response_bytes": 1,
        "max_concurrent_operations": 1,
        "max_processes": 1,
        "max_operation_duration_ms": 1,
        "max_output_preview_bytes": 1,
        "max_output_bytes_per_stream": 1,
        "max_transfer_frame_bytes": 25,
        "max_concurrent_file_transfers": 1,
        "max_file_transfer_bytes": 1,
    }


MODEL_TYPES: dict[str, type[BaseModel]] = {
    "InitializeParams": InitializeParams,
    "FileFindParams": FileFindParams,
    "FileStatParams": FileStatParams,
    "FileStatResult": FileStatResult,
    "ShellExecParams": ShellExecParams,
    "OutputReadParams": OutputReadParams,
    "ProcessWriteStdinParams": ProcessWriteStdinParams,
    "ReceiptGetParams": ReceiptGetParams,
    "EIPError": EIPError,
}


def test_generated_surface_covers_eip_v1() -> None:
    assert EIP_PROTOCOL_VERSION == "1.0"
    assert EIP_PROTO_PACKAGE == "a13n.agent_envd.eip.v1"
    assert len(METHODS) == 34
    assert len(set(METHODS)) == len(METHODS)
    assert all(method.kind == "request_response" for method in METHODS.values())
    assert all(method.name == name for name, method in METHODS.items())
    assert all(method.introduced == "1.0" for method in METHODS.values())
    assert EIP_ERROR_CODES[ErrorType.INTEGRITY_MISMATCH] == -32061
    assert sum(method.replay_class == "active_only" for method in METHODS.values()) == 18
    assert sum(method.replay_class == "terminal_evidence" for method in METHODS.values()) == 15
    assert [method.name for method in METHODS.values() if method.replay_class == "ledger_external"] == ["initialize"]
    transfer_methods = [method for method in METHODS.values() if method.transfer_action is not None]
    assert len(transfer_methods) == 5
    assert all(method.transfer_direction is not None for method in transfer_methods)


def test_shared_golden_values_round_trip_canonically() -> None:
    fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        model_type = MODEL_TYPES[case["type"]]
        encoded_fixture = json.dumps(case["value"], separators=(",", ":"), sort_keys=True)
        model = decode_model(encoded_fixture, model_type)
        encoded_model = encode_model(model)
        assert encoded_model == encoded_fixture.encode()
        assert json.loads(encoded_model) == case["value"]


def test_generated_data_frame_codec_matches_shared_golden_frames() -> None:
    fixture = json.loads(DATA_FRAME_GOLDEN_PATH.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        frame = DataFrame(
            kind=DataFrameKind[case["kind"].upper()],
            handle=case["handle"],
            offset=case["offset"],
            payload=bytes.fromhex(case["payload_hex"]),
            reset_status=(DataResetStatus[case["reset_status"].upper()] if case["reset_status"] is not None else None),
        )
        expected = bytes.fromhex(case["frame_hex"])
        assert encode_data_frame(frame, max_frame_bytes=1024) == expected
        assert decode_data_frame(expected, max_frame_bytes=1024) == frame


def test_generated_data_frame_codec_rejects_structural_violations() -> None:
    valid = encode_data_frame(
        DataFrame(kind=DataFrameKind.ATTACH, handle="reader-1"),
        max_frame_bytes=1024,
    )
    for index, value in ((0, ord("X")), (4, 2), (5, 99), (10, 1), (7, 1)):
        invalid = bytearray(valid)
        invalid[index] = value
        with pytest.raises(ValueError):
            decode_data_frame(bytes(invalid), max_frame_bytes=1024)
    with pytest.raises(ValueError):
        decode_data_frame(valid[:-1], max_frame_bytes=1024)
    with pytest.raises(ValueError):
        decode_data_frame(valid + b"\x00", max_frame_bytes=1024)
    with pytest.raises(ValueError):
        decode_data_frame(valid, max_frame_bytes=len(valid) - 1)

    invalid_frames = (
        DataFrame(kind=DataFrameKind.END, handle="reader-1", offset=1, payload=b"x"),
        DataFrame(kind=DataFrameKind.RESET, handle="reader-1", offset=1),
        DataFrame(kind=DataFrameKind.ATTACH, handle=""),
        DataFrame(kind=DataFrameKind.CHUNK, handle="reader-1", offset=2**64 - 1, payload=b"x"),
    )
    for frame in invalid_frames:
        with pytest.raises(ValueError):
            encode_data_frame(frame, max_frame_bytes=1024)


def test_encoder_revalidates_mutated_collection_values() -> None:
    environment = CommandEnvironment(set={"A": "valid"})
    environment.set["A"] = cast(str, 1)
    with pytest.raises(ValueError):
        encode_model(environment)


def test_explicit_wire_defaults_are_applied_but_omitted_canonically() -> None:
    params = ShellExecParams.model_validate(
        {
            "context": {"operation_id": "op-defaults"},
            "request": {
                "command": {
                    "kind": "argv",
                    "executable_spec": {"kind": "name", "name": "true"},
                    "arguments": [],
                },
                "cwd": {"mount_id": "workspace", "path": "/repo"},
                "environment": {"set": {}, "unset": []},
                "network": "configured",
                "limits": {},
                "keep_stdin_open": False,
            },
        }
    )

    assert params.request.network is CommandNetwork.CONFIGURED
    assert params.request.environment.set == {}
    assert params.request.environment.unset == ()
    assert params.request.limits.wall_time_ms is None
    encoded_request = json.loads(encode_model(params))["request"]
    assert encoded_request["command"] == {
        "executable_spec": {"kind": "name", "name": "true"},
        "kind": "argv",
    }
    assert "network" not in encoded_request
    assert "environment" not in encoded_request
    assert "limits" not in encoded_request
    assert "keep_stdin_open" not in encoded_request


def test_eip_limits_define_valid_output_bounds() -> None:
    limits = valid_eip_limits()
    assert EIPLimits.model_validate(limits).max_output_bytes_per_stream == 1

    with pytest.raises(ValidationError):
        EIPLimits.model_validate({**limits, "max_request_bytes": 0})
    with pytest.raises(ValidationError, match="max_output_preview_bytes cannot exceed"):
        EIPLimits.model_validate({**limits, "max_output_preview_bytes": 2})
    with pytest.raises(ValidationError):
        EIPLimits.model_validate({**limits, "max_transfer_frame_bytes": 24})
    with pytest.raises(ValidationError):
        EIPLimits.model_validate({**limits, "max_file_transfer_bytes": 0})


def test_jsonrpc_integer_ids_use_signed_64_bit_range() -> None:
    request = {"jsonrpc": "2.0", "id": 2**63 - 1, "method": "environment.describe", "params": {}}
    assert JsonRpcRequest.model_validate(request).id == 2**63 - 1
    with pytest.raises(ValidationError):
        JsonRpcRequest.model_validate({**request, "id": 2**63})

    success = {"jsonrpc": "2.0", "id": -(2**63), "result": {}}
    assert JsonRpcSuccessResponse.model_validate(success).id == -(2**63)
    with pytest.raises(ValidationError):
        JsonRpcSuccessResponse.model_validate({**success, "id": -(2**63) - 1})

    error = {
        "jsonrpc": "2.0",
        "id": 2**63 - 1,
        "error": {
            "code": -32603,
            "message": "internal error",
            "data": {"error_type": "internal_error", "retry_hint": "never", "dispatch_stage": "unknown"},
        },
    }
    assert JsonRpcErrorResponse.model_validate(error).id == 2**63 - 1
    with pytest.raises(ValidationError):
        JsonRpcErrorResponse.model_validate({**error, "id": 2**63})
    with pytest.raises(ValidationError):
        JsonRpcErrorResponse.model_validate({key: value for key, value in error.items() if key != "id"})


def test_jsonrpc_envelope_ignores_extensions_but_reserves_eip_namespace() -> None:
    request = JsonRpcRequest.model_validate(
        {
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "environment.describe",
            "params": {},
            "trace_context": "optional-extension",
        }
    )
    assert request.id == "request-1"
    assert "trace_context" not in json.loads(encode_model(request))

    with pytest.raises(ValidationError, match="unknown reserved JSON-RPC field"):
        JsonRpcRequest.model_validate(
            {
                "jsonrpc": "2.0",
                "id": "request-1",
                "method": "environment.describe",
                "params": {},
                "eip_authority": "unexpected",
            }
        )
    with pytest.raises(ValidationError):
        JsonRpcRequest.model_validate({"jsonrpc": "2.0", "id": True, "method": "environment.describe", "params": {}})


def test_decoder_rejects_duplicate_and_unknown_authority_fields() -> None:
    payload = '{"context":{"operation_id":"one","operation_id":"two"},"path":{"mount_id":"workspace","path":"/repo"}}'
    with pytest.raises(ValueError, match="duplicate JSON field"):
        decode_model(payload, FileStatParams)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        decode_model(
            '{"context":{"operation_id":"one","principal":"caller"},"path":{"mount_id":"workspace","path":"/repo"}}',
            FileStatParams,
        )


def test_eip_profile_rejects_out_of_range_numbers_and_non_utc_timestamps() -> None:
    with pytest.raises(ValidationError):
        EIPError.model_validate(
            {
                "code": -32603,
                "message": "invalid generation",
                "data": {
                    "error_type": "internal_error",
                    "retry_hint": "never",
                    "dispatch_stage": "pre_dispatch",
                    "generation": 0,
                },
            }
        )
    with pytest.raises(ValidationError):
        EIPError.model_validate(
            {
                "code": 2**31,
                "message": "invalid",
                "data": {"error_type": "internal_error", "retry_hint": "never", "dispatch_stage": "pre_dispatch"},
            }
        )
    with pytest.raises(ValidationError):
        OutputReadParams.model_validate(
            {
                "context": {"operation_id": "op"},
                "reference": "output-1",
                "start_offset": 2**64,
            }
        )
    with pytest.raises(ValidationError):
        EIPCallContext.model_validate({"operation_id": "op", "timeout_ms": 0})
    with pytest.raises(ValidationError):
        EIPCallContext.model_validate({"operation_id": "op", "timeout_ms": "1"})

    context = EIPCallContext.model_validate({"operation_id": "op", "timeout_ms": 1_234})
    assert context.timeout_ms == 1_234
    assert json.loads(encode_model(context))["timeout_ms"] == 1_234


def test_operation_ids_are_bounded_consistently() -> None:
    maximum = "🧪" * 128
    assert EIPCallContext(operation_id=maximum).operation_id == maximum
    with pytest.raises(ValidationError):
        EIPCallContext(operation_id=maximum + "x")

    selector = {"context": {"operation_id": "lookup"}, "operation_id": maximum}
    assert ReceiptGetParams.model_validate(selector).operation_id == maximum
    with pytest.raises(ValidationError):
        ReceiptGetParams.model_validate({**selector, "operation_id": maximum + "x"})


def test_eip_profile_rejects_noncanonical_paths_and_base64() -> None:
    with pytest.raises(ValidationError):
        FileStatParams.model_validate(
            {"context": {"operation_id": "op"}, "path": {"mount_id": "workspace", "path": "/repo/../secret"}}
        )
    with pytest.raises(ValidationError):
        EncodedBytes(encoding="base64", data="aGVsbG8=")
    with pytest.raises(ValidationError):
        EncodedBytes(encoding="base64", data="AB")


def test_completed_reader_requires_structural_digest_evidence() -> None:
    with pytest.raises(ValidationError):
        FileReadCompletion.model_validate({"produced_bytes": 0})


def test_sha256_digest_profile_is_exact_and_lowercase() -> None:
    assert ContentDigest(algorithm="sha256", value="a" * 64).value == "a" * 64
    with pytest.raises(ValidationError):
        ContentDigest(algorithm="sha256", value="A" * 64)
    with pytest.raises(ValidationError):
        ContentDigest(algorithm="sha256", value="a" * 63)


def test_output_info_rejects_unusable_structural_states() -> None:
    base = {
        "producer_complete": True,
        "content_complete": True,
        "produced_bytes": 1,
        "retained_bytes": 1,
        "preview": {"encoding": "base64", "data": "YQ"},
    }
    with pytest.raises(ValidationError):
        OutputInfo.model_validate(base)
    output = OutputInfo.model_validate({**base, "reference": "output-1"})
    assert output.reference.root == "output-1"
    with pytest.raises(ValidationError, match="complete output must retain every produced byte"):
        OutputInfo.model_validate({**base, "reference": "output-1", "produced_bytes": 2})
    with pytest.raises(ValidationError):
        OutputInfo.model_validate({**base, "reference": "output-1", "captured_bytes": 1})


def test_receipt_lookup_requires_an_operation_id() -> None:
    base = {"context": {"operation_id": "op-query"}}
    with pytest.raises(ValidationError):
        ReceiptGetParams.model_validate(base)
    with pytest.raises(ValidationError):
        ReceiptGetParams.model_validate({**base, "receipt_ref": "receipt-1", "operation_id": "op-target"})

    assert ReceiptGetParams.model_validate({**base, "operation_id": "op-target"}).operation_id == "op-target"


def test_error_rejects_removed_retention_fields_and_identity() -> None:
    common = {"retry_hint": "never", "dispatch_stage": "completed"}
    with pytest.raises(ValidationError):
        EIPError.model_validate(
            {
                "code": -32022,
                "message": "gap",
                "data": {**common, "error_type": "retention_gap"},
            }
        )
    with pytest.raises(ValidationError):
        EIPError.model_validate(
            {
                "code": -32603,
                "message": "wrong",
                "data": {**common, "error_type": "internal_error", "available_start": 0},
            }
        )


def test_output_read_requires_an_explicit_offset() -> None:
    base = {
        "context": {"operation_id": "op"},
        "reference": "output-1",
    }
    with pytest.raises(ValidationError):
        OutputReadParams.model_validate(base)
    with pytest.raises(ValidationError):
        OutputReadParams.model_validate({**base, "cursor": "cursor-1", "start_offset": 0})
    assert OutputReadParams.model_validate({**base, "start_offset": 0}).start_offset == 0
