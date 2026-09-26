"""Ingress rejects chunked excess and slow uploads before invoking the application, and every failure a route
lets escape answers in the error envelope."""

import asyncio
import json

import httpx2
import pytest
from a13n_service.infra.http import ErrorEnvelope, install_error_envelope
from a13n_service.infra.ingress import BodyLimit
from a13n_service.infra.redis import rate_limit
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("case", ["bounded", "oversize", "timeout", "disconnect"])
async def test_body_bounds_and_disconnect_forwarding(case):
    queue = asyncio.Queue()
    sent, received = [], []
    if case != "timeout":
        for message in (
            [{"type": "http.disconnect"}]
            if case == "disconnect"
            else [
                {"type": "http.request", "body": b"ab", "more_body": True},
                {"type": "http.request", "body": b"cd" if case == "bounded" else b"cde"},
                {"type": "http.disconnect"},
            ]
        ):
            queue.put_nowait(message)

    async def app(scope, receive, send):
        received.append(await receive())
        received.append(await receive())

    async def send(message):
        sent.append(message)

    await BodyLimit(app, max_bytes=4, timeout=0.02)({"type": "http"}, queue.get, send)
    if case == "bounded":
        assert received == [
            {"type": "http.request", "body": b"abcd", "more_body": False},
            {"type": "http.disconnect"},
        ]
        assert not sent
    else:
        assert not received
        if case == "disconnect":
            assert not sent
        else:
            assert sent[0]["status"] == (413 if case == "oversize" else 408)


async def test_escaping_failures_answer_in_the_error_envelope(caplog: pytest.LogCaptureFixture) -> None:
    app = FastAPI()
    install_error_envelope(app)

    @app.get("/database")
    async def database() -> None:
        raise OperationalError("SELECT secret_sql", {"credential": "secret_parameter"}, Exception("secret_connection"))

    @app.get("/defect")
    async def defect() -> None:
        try:
            raise ValueError("secret_cause")
        except ValueError as cause:
            error = KeyError("secret_message")
            error.add_note("secret_note")
            raise error from cause

    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        unavailable = await client.get("/database")
        internal = await client.get("/defect")
        unknown = await client.get("/nowhere")
        wrong_method = await client.post("/database")
    for response in (unavailable, internal, unknown, wrong_method):
        ErrorEnvelope.model_validate(response.json())
        # One ID per request, in the body and once in the header, also for a defect answered outside the middleware.
        assert response.headers.get_list("x-request-id") == [response.json()["error"]["request_id"]]
        assert response.headers["cache-control"] == "no-store"
    # No route answers a method and path alike, whether the path is unknown or only the method is wrong.
    for response, route in ((unknown, "GET /nowhere"), (wrong_method, "POST /database")):
        assert response.status_code == 404
        assert response.json()["error"]["details"] == {"kind": "route", "id": route}
    assert unavailable.status_code == 503
    error = unavailable.json()["error"]
    assert error.pop("request_id").startswith("req_")
    assert error == {
        "code": "unavailable",
        "message": "The database is unavailable",
        "details": {"dependency": "database"},
    }
    assert internal.status_code == 500
    error = internal.json()["error"]
    identity = error.pop("request_id")
    assert error == {"code": "internal", "message": "Internal error", "details": {}}
    # The defect names its cause and source locations without copying exception text into logs.
    [logged] = [record for record in caplog.records if record.getMessage() == "Unhandled error"]
    assert logged.request_id == identity  # type: ignore[attr-defined]
    assert logged.exc_info is None
    details = logged.exception_details  # type: ignore[attr-defined]
    assert [item["type"] for item in details] == ["builtins.KeyError", "builtins.ValueError"]
    assert details[1]["parent"] == 0
    assert all(item["frames"][-1]["function"] == "defect" for item in details)
    [dependency] = [record for record in caplog.records if record.getMessage() == "Dependency unavailable"]
    assert dependency.exception_details[0]["frames"][-1]["function"] == "database"  # type: ignore[attr-defined]
    for record in (logged, dependency):
        assert "secret_" not in json.dumps(vars(record))
    assert "secret_" not in caplog.text


async def test_rate_limits_are_not_enforced_while_redis_is_down() -> None:
    unreachable = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    try:
        for _ in range(3):
            await rate_limit(unreachable, "login:someone", limit=1, window_seconds=60)
    finally:
        await unreachable.aclose()
