from a13n_service.request_runtime import get_process_runtime
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


def test_process_runtime_rejects_untyped_application_state() -> None:
    app = FastAPI()
    app.state.runtime = object()

    assert get_process_runtime(_request(app)) is None


def test_process_runtime_accepts_typed_application_state(process_runtime_factory) -> None:
    app = FastAPI()
    runtime = process_runtime_factory()
    app.state.runtime = runtime

    assert get_process_runtime(_request(app)) is runtime
