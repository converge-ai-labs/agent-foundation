"""Native OpenAPI metadata shared by interactive docs and SDK generation."""

import re
from copy import deepcopy

from fastapi import FastAPI
from pydantic import BaseModel

from a13n_service.gateway.notifications import NotificationSubscription
from a13n_service.run_stream.domain import RunStreamEvent


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, object]
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


ETAG_HEADERS = {"ETag": {"description": "Strong representation precondition.", "schema": {"type": "string"}}}
BINARY_RESPONSE: dict[int | str, dict[str, object]] = {
    200: {
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        "headers": ETAG_HEADERS,
    }
}


def install_openapi(app: FastAPI) -> None:
    """Enrich the framework schema without starting Service resources."""
    framework_openapi = app.openapi

    def openapi() -> dict:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = framework_openapi()
        models = schema.setdefault("components", {}).setdefault("schemas", {})
        for model in (ErrorResponse, RunStreamEvent, NotificationSubscription):
            definition = model.model_json_schema(ref_template="#/components/schemas/{model}")
            models.update(definition.pop("$defs", {}))
            models[model.__name__] = definition
        # FastAPI gives input/output variants the same title. Preserve their distinct identities.
        for name, definition in models.items():
            definition["title"] = name
        error = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}
        for path, item in schema["paths"].items():
            if not path.startswith("/api/v1/"):
                continue
            for method, operation in item.items():
                if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                # Path and method own the wire identity; Python function names do not.
                name = re.sub(r"[^a-zA-Z0-9]+", "_", path.removeprefix("/api/v1/")).strip("_")
                operation["operationId"] = f"{method}_{name}"
                for parameter in operation.get("parameters", []):
                    if parameter.get("in") == "header" and parameter["name"].lower() == "if-match":
                        # Search keeps the dependency optional only to return its specific 428 error.
                        parameter["required"] = True
                        parameter["schema"] = {"type": "string", "minLength": 1, "maxLength": 256}
                responses = operation["responses"]
                validation = responses.get("422", {}).get("content", {}).get("application/json", {}).get("schema", {})
                if validation.get("$ref") == "#/components/schemas/HTTPValidationError":
                    del responses["422"]
                    responses["400"] = {"description": "Invalid request.", "content": deepcopy(error)}
                responses.setdefault("default", {"description": "Service error.", "content": deepcopy(error)})
                for response in responses.values():
                    response.setdefault("headers", {})["X-Request-ID"] = {"schema": {"type": "string"}}
                responses["default"]["headers"]["Retry-After"] = {"schema": {"type": "string"}}
        return schema

    app.openapi = openapi
