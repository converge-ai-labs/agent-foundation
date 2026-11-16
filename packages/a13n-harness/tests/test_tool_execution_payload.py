from __future__ import annotations

import json

import pytest
from a13n_harness.tool_execution_payload import ToolExecutionPayload
from pydantic import ValidationError
from pydantic_ai import BinaryContent, ToolReturn


def test_strict_payload_loading_uses_native_content_codec_without_rehydrating_result_data() -> None:
    application_data = {"kind": "document-url", "url": "file:///untrusted/not-a-file", "media_type": "text/plain"}
    payload = ToolExecutionPayload(
        wrapped=True,
        result=ToolReturn(
            return_value=application_data,
            content=[BinaryContent(data=b"\x00\xffevidence", media_type="image/png")],
        ),
        artifacts=[],
    )
    restored = ToolExecutionPayload.model_validate_json(payload.model_dump_json(), strict=True)
    assert restored.result.return_value == application_data
    assert isinstance(restored.result.return_value, dict)
    assert isinstance(restored.result.content, list)
    assert isinstance(restored.result.content[0], BinaryContent)
    assert restored.result.content[0].data == b"\x00\xffevidence"


def test_native_content_decoding_does_not_relax_outer_payload_fields() -> None:
    payload = ToolExecutionPayload(wrapped=True, result=ToolReturn(return_value="ok"), artifacts=[])
    serialized = json.loads(payload.model_dump_json())
    serialized["wrapped"] = "true"
    with pytest.raises(ValidationError, match="wrapped"):
        ToolExecutionPayload.model_validate_json(json.dumps(serialized), strict=True)
