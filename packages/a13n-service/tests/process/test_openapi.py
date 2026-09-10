"""The schema consumed by SDKs describes actual Native HTTP behavior."""

from a13n_service.app import create_app
from a13n_service.openapi import ErrorResponse
from a13n_service.settings import Settings

from .support import request


def test_native_openapi_is_cached_and_operations_have_wire_names() -> None:
    app = create_app(Settings())
    schema = app.openapi()
    assert app.openapi() is schema
    operations = [
        operation
        for path, methods in schema["paths"].items()
        if path.startswith("/api/v1/")
        for operation in methods.values()
    ]
    identities = [operation["operationId"] for operation in operations]
    assert len(identities) == len(set(identities))
    assert schema["paths"]["/api/v1/auth/context"]["get"]["operationId"] == "get_auth_context"
    assert all(name == definition["title"] for name, definition in schema["components"]["schemas"].items())


def test_authentication_is_described_on_protected_not_public_operations() -> None:
    schema = create_app(Settings()).openapi()
    assert schema["paths"]["/api/v1/auth/context"]["get"]["security"] == [
        {"BearerAuth": []},
        {"SessionAuth": []},
    ]
    assert (
        schema["paths"]["/api/v1/oauth/mcp/callback"]["get"]["security"]
        == schema["paths"]["/api/v1/auth/context"]["get"]["security"]
    )
    assert "security" not in schema["paths"]["/api/v1/auth/login"]["post"]
    assert schema["components"]["securitySchemes"]["SessionAuth"]["name"] == "a13n_session"


def test_native_errors_use_the_runtime_envelope() -> None:
    app = create_app(Settings())
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/workspaces/{workspace}/search-providers"]["post"]
    assert "422" not in operation["responses"]
    for status in ("400", "default"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
    response = request(app, "/api/v1/auth/context")
    assert response.status_code == 401
    error = ErrorResponse.model_validate(response.json())
    assert error.error.code == "authentication_required"
    assert error.error.request_id == response.headers["X-Request-ID"]


def test_binary_streams_and_preconditions_are_explicit() -> None:
    paths = create_app(Settings()).openapi()["paths"]
    asset = paths["/api/v1/assets/{asset_id}/content"]["get"]["responses"]["200"]
    assert asset["content"] == {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}
    assert "ETag" in asset["headers"]
    image = paths["/api/v1/users/{user_id}/avatar/{image_id}"]["get"]["responses"]["200"]
    assert set(image["content"]) == {"image/webp"}
    stream = paths["/api/v1/runs/{run_id}/stream"]["get"]["responses"]["200"]
    assert set(stream["content"]) == {"text/event-stream"}
    search = paths["/api/v1/workspaces/{workspace}/search-providers/{provider_id}"]["patch"]
    header = next(parameter for parameter in search["parameters"] if parameter["name"] == "If-Match")
    assert header["required"] is True
    assert header["schema"]["type"] == "string"
    assert "ETag" in search["responses"]["200"]["headers"]
