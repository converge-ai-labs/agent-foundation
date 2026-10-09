"""Protocol fidelity and operation confinement of the single generated HTTP adapter."""

import json
from typing import Literal

import pytest
from a13n_service.api_tools import api_tools, tool_name
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
            result = await mapped["update_item"].run({"item_id": "one", "if-match": '"item:1"', "request_body": body})
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
