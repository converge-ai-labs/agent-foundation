from a13n_service.request_runtime import get_service_runtime
from fastapi import FastAPI, Request


def _request(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "path": "/",
            "headers": [],
        }
    )


def test_service_runtime_rejects_untyped_application_state() -> None:
    app = FastAPI()
    app.state.runtime = object()

    assert get_service_runtime(_request(app)) is None


def test_service_runtime_accepts_typed_application_state(service_runtime_factory) -> None:
    app = FastAPI()
    runtime = service_runtime_factory()
    app.state.runtime = runtime

    assert get_service_runtime(_request(app)) is runtime
