"""Protocol fidelity and operation confinement of the single generated HTTP adapter."""

import json
from copy import deepcopy
from typing import Literal
from unittest.mock import patch

import pytest
from a13n_service.api_tools import api_tools, tool_name
from a13n_service.infra.http import MCP_ENTITY_TAG, ErrorEnvelope, IfMatch, etag, install_error_envelope, require_match
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastmcp.exceptions import ToolError
from fastmcp.server.http import set_http_request
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request as HttpRequest

pytestmark = pytest.mark.anyio
ADMIT = {"x-a13n-mcp": True}


def tools(app: FastAPI):
    return {
        tool.name: tool
        for tool in api_tools(
            app, app.openapi(), "http://service.local", [route for route in app.routes if isinstance(route, APIRoute)]
        )
    }


def caller():
    return set_http_request(HttpRequest({"type": "http", "headers": [(b"authorization", b"Bearer test-key")]}))


class Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


class Managed(BaseModel):
    kind: Literal["managed"]
    config: Nested


class External(BaseModel):
    kind: Literal["external"]
    endpoint: str


async def test_generated_schemas_bodies_metadata_errors_and_no_cookie_state() -> None:
    app = FastAPI()
    seen = []

    @app.patch("/items/{item_id}", operation_id="update_item", openapi_extra=ADMIT)
    async def update(item_id: str, body: Nested, request: Request, if_match: str | None = Header(None)) -> Nested:
        seen.append((await request.json(), dict(request.headers)))
        return JSONResponse(
            body.model_dump(), headers={"etag": '"item:2"', "set-cookie": "session=secret", "x-request-id": "req_test"}
        )

    @app.post("/union", operation_id="create_union", openapi_extra=ADMIT)
    async def union(body: Managed | External) -> Managed | External:
        return body

    @app.delete("/items/{item_id}", operation_id="delete_item", status_code=204, openapi_extra=ADMIT)
    async def delete(item_id: str) -> Response:
        return Response(status_code=204)

    mapped = tools(app)
    schema = mapped["create_union"].parameters
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate({"request_body": {"kind": "managed", "config": {"note": None}}})
    Draft202012Validator(schema).validate({"request_body": {"kind": "external", "endpoint": "x"}})
    assert not Draft202012Validator(schema).is_valid({"request_body": {"kind": "managed", "config": {"unknown": True}}})
    with caller():
        for body in ({"note": None}, {}):
            result = await mapped["update_item"].run({"item_id": "one", "if_match": '"item:1"', "request_body": body})
            value = result.structured_content
            assert value["body"] == {"note": None}
            assert value["headers"] == {"etag": '"item:2"', "x-request-id": "req_test"}
            Draft202012Validator(mapped["update_item"].output_schema).validate(value)
            assert seen[-1][0] == body
            assert seen[-1][1]["if-match"] == '"item:1"'
            assert seen[-1][1]["authorization"] == "Bearer test-key"
            assert "cookie" not in seen[-1][1]
        result = await mapped["delete_item"].run({"item_id": "one"})
        assert result.structured_content == {"status": 204, "headers": {}, "body": None}
        Draft202012Validator(mapped["delete_item"].output_schema).validate(result.structured_content)
        with pytest.raises(ToolError, match="Unknown"):
            await mapped["update_item"].run({"item_id": "one", "authorization": "forged"})


async def test_path_arguments_cannot_change_operation_or_parameter_boundaries() -> None:
    app = FastAPI()
    calls = []

    @app.get("/items/special")
    async def special() -> dict:
        calls.append("special")
        return {}

    @app.get("/items/{item_id}", operation_id="item", openapi_extra=ADMIT)
    async def item(item_id: str) -> dict:
        calls.append("item")
        return {"id": item_id}

    @app.get("/items/{item_id}/content")
    async def download(item_id: str) -> Response:
        calls.append("download")
        return Response(b"secret", media_type="application/octet-stream")

    @app.get("/memories/{memory_id}/files/{path:path}", operation_id="file", openapi_extra=ADMIT)
    async def file(memory_id: str, path: str) -> dict:
        calls.append("file")
        return {"memory_id": memory_id, "path": path}

    mapped = tools(app)
    with caller():
        for value in ("x/content", "special", "", "../../items/special"):
            with pytest.raises(ToolError):
                await mapped["item"].run({"item_id": value})
        with pytest.raises(ToolError):
            await mapped["file"].run({"memory_id": "m/files/a", "path": "b.md"})
        assert calls == []
        result = await mapped["file"].run({"memory_id": "m", "path": "a/b.md"})
        assert result.structured_content["body"] == {"memory_id": "m", "path": "a/b.md"}
        encoded = await mapped["item"].run({"item_id": "x%2Fcontent"})
        assert encoded.structured_content["body"] == {"id": "x%2Fcontent"}


def test_error_schema_is_generated_once_per_assembly_and_isolated_per_tool() -> None:
    app = FastAPI()

    @app.get("/first", operation_id="first", openapi_extra=ADMIT)
    @app.get("/second", operation_id="second", openapi_extra=ADMIT)
    async def item() -> dict:
        return {}

    app.openapi()
    expected = ErrorEnvelope.model_json_schema()
    definitions = expected.pop("$defs")
    with patch.object(ErrorEnvelope, "model_json_schema", wraps=ErrorEnvelope.model_json_schema) as generate:
        mapped = tools(app)
        assert generate.call_count == 1
        first = mapped["first"].output_schema
        second = mapped["second"].output_schema
        assert first is not None and second is not None
        assert first["properties"]["body"]["anyOf"][0] == expected
        assert all(first["$defs"][name] == definition for name, definition in definitions.items())
        original_first = deepcopy(first)
        original = deepcopy(second)
        first["properties"]["body"]["anyOf"][0]["properties"].clear()
        for name in definitions:
            first["$defs"][name].clear()
        assert second == original
        assert tools(app)["first"].output_schema == original_first
        assert generate.call_count == 2


def test_assembly_rejects_invalid_admission_and_names_are_order_independent() -> None:
    app = FastAPI()

    @app.get(
        "/bytes",
        operation_id="bytes",
        openapi_extra=ADMIT,
        response_class=Response,
        responses={200: {"content": {"application/octet-stream": {}}}},
    )
    async def binary() -> Response:
        return Response(b"bytes")

    with pytest.raises(ValueError, match="JSON responses"):
        tools(app)
    first = tool_name("a" * 90 + "x")
    second = tool_name("a" * 90 + "y")
    assert first != second and len(first) == len(second) == 64
    assert json.dumps(first) == json.dumps(tool_name("a" * 90 + "x"))


async def test_header_arguments_use_snake_case_names_and_reach_their_http_headers() -> None:
    app = FastAPI()
    seen = []

    @app.post("/items/{item_id}/runs", operation_id="start_item", openapi_extra=ADMIT)
    async def start(
        item_id: str,
        body: Nested,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        if_match: str | None = Header(None, alias="If-Match"),
    ) -> dict:
        seen.append(dict(request.headers))
        return {}

    @app.get("/items/{item_id}/events", operation_id="item_events", openapi_extra=ADMIT)
    async def events(item_id: str, request: Request, last_event_id: str | None = Header(None, alias="Last-Event-ID")):
        seen.append(dict(request.headers))
        return {}

    mapped = tools(app)
    start_schema = mapped["start_item"].parameters
    assert set(start_schema["properties"]) == {"item_id", "idempotency_key", "if_match", "request_body"}
    assert set(start_schema["required"]) == {"item_id", "idempotency_key", "request_body"}
    assert set(mapped["item_events"].parameters["properties"]) == {"item_id", "last_event_id"}
    for tool in mapped.values():
        assert not {"If-Match", "Idempotency-Key", "Last-Event-ID"} & tool.parameters["properties"].keys()
    with caller():
        result = await mapped["start_item"].run(
            {"item_id": "one", "idempotency_key": "key-1", "if_match": '"item:1"', "request_body": {}}
        )
        assert result.structured_content["status"] == 200
        assert seen[-1]["idempotency-key"] == "key-1" and seen[-1]["if-match"] == '"item:1"'
        await mapped["item_events"].run({"item_id": "one", "last_event_id": "1-2"})
        assert seen[-1]["last-event-id"] == "1-2"
        # The HTTP spelling is not an argument name; the rejection names the replacement instead.
        with pytest.raises(ToolError) as rejected:
            await mapped["start_item"].run(
                {"item_id": "one", "Idempotency-Key": "secret-key-value", "request_body": {}}
            )
        message = str(rejected.value)
        assert "Idempotency-Key" in message
        assert "idempotency_key" in message and "if_match" in message and "request_body" in message
        assert "secret-key-value" not in message
        assert len(seen) == 2


def test_header_argument_names_must_stay_unique() -> None:
    app = FastAPI()

    @app.get("/items/{item_id}", operation_id="item", openapi_extra=ADMIT)
    async def item(item_id: str, header: str | None = Header(None, alias="Item-Id")) -> dict:
        return {}

    with pytest.raises(ValueError, match="item_id collides"):
        tools(app)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def test_writes_take_expected_version_and_send_a_quoted_if_match() -> None:
    app = FastAPI()
    install_error_envelope(app)
    seen = []
    version = {"value": 3}

    @app.get("/items/{item_id}", operation_id="read_item", openapi_extra=ADMIT)
    async def read(item_id: str, response: Response) -> dict:
        response.headers["etag"] = etag(item_id, version["value"])
        return {"id": item_id, "version": version["value"]}

    @app.patch("/items/{item_id}", operation_id="change_item", openapi_extra=ADMIT)
    async def change(item_id: str, body: Nested, request: Request, if_match: IfMatch = None) -> dict:
        seen.append(request.headers.get("if-match"))
        require_match(if_match, item_id, version["value"])
        version["value"] += 1
        return {"id": item_id, "version": version["value"]}

    @app.post("/items/{item_id}/pause", operation_id="pause_item", status_code=204, openapi_extra=ADMIT)
    async def pause(item_id: str, body: Command, request: Request, if_match: IfMatch = None) -> Response:
        seen.append((request.headers.get("if-match"), await request.json()))
        require_match(if_match, item_id, version["value"])
        version["value"] += 1
        return Response(status_code=204)

    mapped = tools(app)
    change_schema = mapped["change_item"].parameters
    assert set(change_schema["properties"]) == {"item_id", "expected_version", "request_body"}
    assert "expected_version" not in change_schema["required"]
    description = change_schema["properties"]["expected_version"]["description"]
    assert "sch_x:3" in description and '"sch_x:3"' in description
    assert MCP_ENTITY_TAG not in json.dumps(change_schema)
    assert set(mapped["read_item"].parameters["properties"]) == {"item_id"}
    # A body that admits only `{}` is optional.
    pause_schema = mapped["pause_item"].parameters
    assert set(pause_schema["properties"]) == {"item_id", "expected_version", "request_body"}
    assert pause_schema["required"] == ["item_id"]
    with caller():
        read = (await mapped["read_item"].run({"item_id": "sch_x"})).structured_content
        assert read["headers"]["etag"] == '"sch_x:3"'
        unquoted = await mapped["change_item"].run(
            {"item_id": "sch_x", "expected_version": "sch_x:3", "request_body": {}}
        )
        assert unquoted.structured_content["status"] == 200 and seen[-1] == '"sch_x:3"'
        quoted = await mapped["change_item"].run(
            {"item_id": "sch_x", "expected_version": '"sch_x:4"', "request_body": {"note": None}}
        )
        assert quoted.structured_content["status"] == 200 and seen[-1] == '"sch_x:4"'
        stale = (
            await mapped["change_item"].run({"item_id": "sch_x", "expected_version": "sch_x:4", "request_body": {}})
        ).structured_content
        assert stale["status"] == 412 and stale["body"]["error"]["details"]["current_etag"] == '"sch_x:5"'
        missing = (await mapped["change_item"].run({"item_id": "sch_x", "request_body": {}})).structured_content
        assert missing["status"] == 428 and missing["body"]["error"]["code"] == "precondition_required"
        assert seen[-1] is None
        paused = await mapped["pause_item"].run({"item_id": "sch_x", "expected_version": "sch_x:5"})
        assert paused.structured_content["status"] == 204 and paused.structured_content["body"] is None
        assert seen[-1] == ('"sch_x:5"', {})
        with pytest.raises(ToolError) as rejected:
            await mapped["change_item"].run({"item_id": "sch_x", "if_match": '"sch_x:6"', "request_body": {}})
        assert "if_match" in str(rejected.value) and "expected_version" in str(rejected.value)
