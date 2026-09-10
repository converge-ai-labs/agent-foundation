"""Shared wire fixtures and real generated transport calls, not schema snapshots."""

import asyncio
import json
from pathlib import Path

import httpx2 as httpx
import pytest

from a13n import Client
from a13n.generated.api.asset_management import get_assets_asset_id_content
from a13n.generated.api.identity import get_auth_context
from a13n.generated.models import (
    AgentInput,
    AgentInputSchemaVersion,
    ConnectorCollection,
    CreateSearchProviderRequest,
    CreateSearchProviderRequestType,
    ExistingEnvironmentSelection,
    NewEnvironmentSelection,
    PrincipalRef,
    RunStatus,
    SystemActorRef,
    ThreadRunSubmissionRequest,
    UpdateAgentRequest,
    UsageLimitsInput,
    UserMessage,
)
from a13n.generated.types import UNSET

FIXTURES = json.loads((Path(__file__).parents[2] / "fixtures/wire.json").read_text())


def test_wire_fixtures_roundtrip() -> None:
    for key, model in [
        ("patch", UpdateAgentRequest),
        ("user_message", UserMessage),
        ("null_cursor", ConnectorCollection),
    ]:
        for value in FIXTURES[key]:
            assert model.from_dict(value).to_dict() == value
    for value in FIXTURES["actor"]:
        actor = (
            SystemActorRef.from_dict(value) if value["principal_type"] == "system" else PrincipalRef.from_dict(value)
        )
        assert actor.to_dict() == value
    for value in FIXTURES["environment"]:
        wire = {"expected_thread_version": 1, "input": {"schema_version": "2"}, "environment": value}
        request = ThreadRunSubmissionRequest.from_dict(wire)
        assert isinstance(request.environment, ExistingEnvironmentSelection | NewEnvironmentSelection)
        assert request.to_dict() == wire
    for value in FIXTURES["cost"]:
        assert UsageLimitsInput.from_dict({"cost_limit": value}).to_dict() == {"cost_limit": value}
    for value in FIXTURES["run_status"]:
        assert RunStatus(value).value == value
    assert UpdateAgentRequest().name is UNSET
    assert UpdateAgentRequest(name=None).name is None
    assert ThreadRunSubmissionRequest(
        expected_thread_version=1, input_=AgentInput(schema_version=AgentInputSchemaVersion.VALUE_1)
    ).to_dict() == {
        "expected_thread_version": 1,
        "input": {"schema_version": "2"},
    }
    secret = CreateSearchProviderRequest(
        type_=CreateSearchProviderRequestType.BRAVE, name="test", credential="do-not-print"
    )
    assert "do-not-print" not in repr(secret)
    assert secret.to_dict()["credential"] == "do-not-print"


def test_generated_calls_share_transport_headers_prefix_and_close() -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/prefix/api/v1/auth/context"
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            200, json=FIXTURES["credential_context"], headers={"X-Request-ID": "req_test", "ETag": '"v1"'}
        )

    async def scenario() -> None:
        async with Client(
            "https://service.example/prefix", "test-token", transport=httpx.MockTransport(handler)
        ) as client:
            result = await client.execute(lambda api: get_auth_context.asyncio_detailed(client=api))
            assert result.parsed is not None and result.parsed.workspace_id == "ws_example"
            assert result.headers["X-Request-ID"] == "req_test"
            assert result.headers["ETag"] == '"v1"'
            workspace = await client.workspace()
            assert workspace is not None
        with pytest.raises(Exception, match="closed"):
            await client.execute(lambda api: get_auth_context.asyncio_detailed(client=api))
        assert len(calls) == 2

    asyncio.run(scenario())


def test_generated_binary_stream_is_lazy_and_cancellable() -> None:
    consumed = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(1)
            yield b"first"
            consumed.append(2)
            yield b"last"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/assets/ast_test/content"
        return httpx.Response(200, stream=Body())

    async def scenario() -> None:
        async with Client("https://service.example", "token", transport=httpx.MockTransport(handler)) as client:
            async with client.stream(get_assets_asset_id_content.build_request("ast_test")) as response:
                assert consumed == []
                async for chunk in response.aiter_bytes():
                    assert chunk == b"first"
                    break
            assert consumed == [1]

    asyncio.run(scenario())


def test_close_cancels_generated_call_without_replay() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def handler(_request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        client = Client("https://service.example", "token", transport=httpx.MockTransport(handler))
        pending = asyncio.create_task(client.execute(lambda api: get_auth_context.asyncio_detailed(client=api)))
        await started.wait()
        await client.aclose()
        assert pending.cancelled()

    asyncio.run(scenario())


def test_typechecker_rejects_untyped_request_fields(tmp_path: Path) -> None:
    import subprocess
    import sys

    source = tmp_path / "invalid.py"
    source.write_text(
        "from a13n.generated.models import UpdateAgentRequest, UserMessage\n"
        "UpdateAgentRequest(name=42)\n"
        "UserMessage(id='m1', content=42)\n"
    )
    result = subprocess.run(
        ["pyright", "--pythonpath", sys.executable, "--outputjson", str(source)], capture_output=True, text=True
    )
    report = json.loads(result.stdout)
    assert result.returncode == 1
    diagnostics = report["generalDiagnostics"]
    assert len(diagnostics) == 2
    assert all(item["rule"] == "reportArgumentType" for item in diagnostics)


def test_generated_default_error_and_diagnostics_are_safe() -> None:
    from a13n.generated.models import ErrorResponse

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "authentication_required",
                    "message": "Sign in",
                    "details": {},
                    "request_id": "req_error",
                }
            },
            headers={"Set-Cookie": "a13n_session=do-not-print"},
        )

    async def scenario() -> None:
        async with Client("https://service.example", "token", transport=httpx.MockTransport(handler)) as client:
            result = await client.execute(lambda api: get_auth_context.asyncio_detailed(client=api))
            assert isinstance(result.parsed, ErrorResponse)
            assert result.parsed.error.code == "authentication_required"
            assert "do-not-print" not in repr(result)

    asyncio.run(scenario())


def test_generated_async_file_upload_uses_bounded_reads() -> None:
    from io import BytesIO

    from a13n.generated.api.identity_images import put_users_me_avatar
    from a13n.generated.types import File

    reads = []

    class Source(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 65536
            reads.append(size)
            return super().read(size)

    source = Source(b"a" * 140000)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/users/me/avatar"
        assert request.headers["If-Match"] == '"v1"'
        assert request.content == b"a" * 140000
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "invalid_image",
                    "message": "Not an image",
                    "details": {},
                    "request_id": "req_image",
                }
            },
        )

    async def scenario() -> None:
        async with Client("https://service.example", "token", transport=httpx.MockTransport(handler)) as client:
            result = await client.execute(
                lambda api: put_users_me_avatar.asyncio_detailed(
                    client=api,
                    body=File(payload=source),
                    if_match='"v1"',
                )
            )
            assert result.status_code == 400
        assert len(reads) >= 3
        assert not source.closed  # input ownership stays with the caller

    asyncio.run(scenario())
