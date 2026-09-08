from __future__ import annotations

import pytest
from a13n_harness import DefinitionError
from a13n_harness.tools import HarnessToolMetadata, ToolOutputPolicy
from a13n_harness.tools.metadata import normalize_harness_tool_metadata
from pydantic import ValidationError


def test_harness_tool_metadata_is_detached_and_normalized() -> None:
    policy = ToolOutputPolicy(max_inline_bytes=512, max_output_bytes=1024)
    metadata = normalize_harness_tool_metadata(
        {
            "tool_id": " files.read ",
            "effects": ["read"],
            "credential_audiences": [" storage "],
            "idempotency": "read_only",
            "output_policy": policy.model_dump(),
            "superseded_by_tool_ids": [" tools.shell "],
        }
    )

    assert isinstance(metadata, HarnessToolMetadata)
    assert metadata.tool_id == "files.read"
    assert metadata.effects == frozenset({"read"})
    assert metadata.credential_audiences == ("storage",)
    assert metadata.output_policy is not policy
    assert metadata.superseded_by_tool_ids == frozenset({"tools.shell"})


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {
            "tool_id": "x",
            "effects": [],
            "credential_audiences": [],
            "idempotency": "none",
            "output_policy": {"max_inline_bytes": 1, "max_output_bytes": 1},
        },
        {
            "tool_id": "x",
            "effects": ["read"],
            "credential_audiences": [],
            "idempotency": "guess",
            "output_policy": {"max_inline_bytes": 1, "max_output_bytes": 1},
        },
    ],
)
def test_invalid_reserved_metadata_fails_instead_of_downgrading(value: object) -> None:
    with pytest.raises(DefinitionError) as exc_info:
        normalize_harness_tool_metadata(value)
    assert exc_info.value.code == "tool_metadata_invalid"


def test_tool_cannot_declare_self_supersession() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessToolMetadata(
            tool_id="files.read",
            effects=frozenset({"read"}),
            credential_audiences=(),
            idempotency="read_only",
            output_policy=ToolOutputPolicy(max_inline_bytes=512, max_output_bytes=1024),
            superseded_by_tool_ids=frozenset({"files.read"}),
        )

    assert exc_info.value.code == "tool_supersession_self_reference"


def test_output_policy_requires_finite_ordered_limits() -> None:
    with pytest.raises(ValidationError):
        ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=128)


def test_credential_audience_uniqueness_is_checked_after_normalization() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessToolMetadata(
            tool_id="files.read",
            effects=frozenset({"read"}),
            credential_audiences=("storage", " storage "),
            idempotency="read_only",
            output_policy=ToolOutputPolicy(max_inline_bytes=512, max_output_bytes=1024),
        )

    assert exc_info.value.code == "tool_metadata_invalid"
