import pytest
from a13n_harness.mcp_inputs import McpInputResponse, McpTaskInputRequest, validate_schema


@pytest.mark.parametrize(
    "field",
    [
        {"format": "password"},
        {"format": "secret"},
        {"writeOnly": True},
        {"$ref": "https://example.test/remote"},
        {"default": "x" * 65536},
    ],
)
def test_mcp_form_rejects_sensitive_or_unsupported_fields(field):
    with pytest.raises(ValueError):
        validate_schema({"type": "object", "properties": {"value": {"type": "string", **field}}})


def test_mcp_form_requires_valid_known_fields_and_bounded_content():
    form = McpTaskInputRequest(
        mode="form",
        message="Choose",
        schema={
            "type": "object",
            "required": ["count"],
            "properties": {"count": {"type": "integer", "minimum": 1}},
        },
    )
    form.validate_response(McpInputResponse(action="accept", content={"count": 1}))
    for values in ({}, {"count": 0}, {"count": 1, "extra": True}):
        with pytest.raises(ValueError):
            form.validate_response(McpInputResponse(action="accept", content=values))


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://name@example.test/", "https://", "https://a\\b.test"])
def test_mcp_rejects_unsafe_url_destinations(url):
    with pytest.raises(ValueError):
        McpTaskInputRequest(mode="url", message="Confirm", url=url).validate_prompt()


def test_mcp_url_confirmation_has_canonical_host_and_no_form_content():
    url = McpTaskInputRequest(mode="url", message="Confirm", url="https://Example.Test:8443/")
    assert url.validate_prompt() == "example.test"
    url.validate_response(McpInputResponse(action="accept"))
    with pytest.raises(ValueError, match="cannot include form data"):
        url.validate_response(McpInputResponse(action="accept", content={}))
