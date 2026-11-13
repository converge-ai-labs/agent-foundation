"""Shared startup recovery is independent of Host policy and business dispatch."""

import asyncio

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings, mcp
from a13n_harness.mcp import InitializationRetryClient, MCPServer
from fastmcp import Client, FastMCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("failures", [1, 2, 3])
async def test_bounded_initialization_retry_and_cleanup(failures, monkeypatch):
    attempts = 0
    delays = []
    original = Client._negotiate

    async def negotiate(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= failures:
            raise RuntimeError("Failed to initialize server session") from TimeoutError()
        return await original(self, *args, **kwargs)

    async def backoff(delay):
        delays.append(delay)

    monkeypatch.setattr(Client, "_negotiate", negotiate)
    monkeypatch.setattr(mcp, "sleep", backoff)
    client = InitializationRetryClient(FastMCP("fixture"))
    if failures == 3:
        with pytest.raises(RuntimeError, match="Failed to initialize"):
            async with client:
                pytest.fail("unreachable")
    else:
        async with client:
            assert client.is_connected()
    assert attempts == min(failures + 1, 3)
    assert delays == [1, 2][: min(failures, 2)]
    assert not client.is_connected()


@pytest.mark.parametrize("failure_source", ["client", "transport"])
async def test_cleanup_failure_preserves_initialization_error_without_retry(monkeypatch, failure_source):
    attempts = 0
    original = TimeoutError("initialization timed out")
    cleanup = ValueError("cleanup failed")

    async def enter(self):
        nonlocal attempts
        attempts += 1
        raise original

    async def close(self):
        raise cleanup if failure_source == "client" else original

    async def close_transport():
        raise cleanup

    async def backoff(delay):
        pytest.fail("A client whose cleanup failed must not be retried")

    monkeypatch.setattr(Client, "__aenter__", enter)
    monkeypatch.setattr(InitializationRetryClient, "close", close)
    monkeypatch.setattr(mcp, "sleep", backoff)
    client = InitializationRetryClient(FastMCP("fixture"))
    monkeypatch.setattr(client.transport, "close", close_transport)
    with pytest.raises(TimeoutError, match="initialization timed out") as failure:
        await client.__aenter__()
    assert failure.value is original and failure.value.__cause__ is cleanup
    assert attempts == 1


async def test_cancellation_during_backoff_does_not_retry(monkeypatch):
    attempts = 0
    backing_off = asyncio.Event()

    async def enter(self):
        nonlocal attempts
        attempts += 1
        raise TimeoutError()

    async def backoff(delay):
        backing_off.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Client, "__aenter__", enter)
    monkeypatch.setattr(mcp, "sleep", backoff)
    task = asyncio.create_task(InitializationRetryClient(FastMCP("fixture")).__aenter__())
    await backing_off.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert attempts == 1


@pytest.mark.parametrize("on_error", [None, "raise", "skip"])
async def test_startup_policy_is_cached_per_run_and_later_run_retries(on_error, caplog):
    openings = 0
    available = False
    inventories, prompts, clients = [], [], []
    server = FastMCP("recovering")

    @server.tool
    def recovered() -> str:
        return "ready"

    async def prepare(ctx):
        async def create_toolset():
            nonlocal openings
            openings += 1
            if not available:
                raise ValueError("private-server-response")
            client = InitializationRetryClient(server)
            clients.append(client)
            return MCPToolset(client, id="recovering")

        return create_toolset

    class CheckedStartup(MCPServer):
        async def for_run(self, ctx):
            first = await super().for_run(ctx)
            assert await super().for_run(ctx) is first
            return first

    healthy = FastMCP("healthy")

    @healthy.tool
    def healthy_tool() -> str:
        return "healthy"

    async def prepare_healthy(ctx):
        async def create_toolset():
            return MCPToolset(InitializationRetryClient(healthy), id="healthy")

        return create_toolset

    async def stream(messages, info):
        inventories.append({tool.name for tool in info.function_tools})
        prompts.append(str(info.instructions))
        yield "continued"

    selected = {} if on_error is None else {"on_error": on_error}
    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            CheckedStartup(id="recovering", prepare=prepare, **selected),
            MCPServer(id="healthy", prepare=prepare_healthy),
        ),
    )
    if on_error != "skip":
        with pytest.raises(ValueError, match="private-server-response"):
            await agent.run("hello", bindings=RunBindings.embedded())
        assert not inventories
    else:
        assert (await agent.run("hello", bindings=RunBindings.embedded())).output_or_raise() == "continued"
        assert inventories == [{"healthy_tool"}]
        assert "recovering is unavailable" in prompts[0]
        warnings = [record for record in caplog.records if record.name.endswith(".mcp")]
        assert len(warnings) == 1 and warnings[0].server_id == "recovering"
        assert "private-server-response" not in str(prompts) + warnings[0].getMessage()
    assert openings == 1
    available = True
    assert (await agent.run("again", bindings=RunBindings.embedded())).output_or_raise() == "continued"
    assert inventories[-1] == {"healthy_tool", "recovered"}
    assert openings == 2
    assert not any(client.is_connected() for client in clients)


async def test_preparation_errors_are_never_skipped():
    async def prepare(ctx):
        raise PermissionError("authority refused")

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(lambda messages, info: "unused"),
        capabilities=(MCPServer(id="denied", prepare=prepare, on_error="skip"),),
    )
    with pytest.raises(PermissionError, match="authority refused"):
        await agent.run("hello", bindings=RunBindings.embedded())


async def test_ready_business_error_does_not_replay():
    calls = 0
    server = FastMCP("fixture")

    @server.tool
    def effect() -> str:
        nonlocal calls
        calls += 1
        raise TimeoutError("effect may already have happened")

    async with InitializationRetryClient(server) as client:
        result = await client.call_tool_mcp("effect", {})
    assert result.is_error and calls == 1


async def test_startup_is_an_explicit_run_binding():
    async def prepare(ctx):
        async def create_toolset():
            raise ValueError("server unavailable")

        return create_toolset

    async def stream(messages, info):
        yield "continued"

    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=stream))
    result = await agent.run(
        "hello",
        bindings=RunBindings.embedded(capabilities=(MCPServer(id="optional", prepare=prepare, on_error="skip"),)),
    )
    assert result.output_or_raise() == "continued"


@pytest.mark.parametrize("tools", [None, (), ("selected",)])
async def test_default_projection_enforces_selected_tool_ceiling(tools):
    server = FastMCP("selection")

    @server.tool
    def selected() -> str:
        return "selected"

    @server.tool
    def unselected() -> str:
        return "unselected"

    async def prepare(ctx):
        async def create_toolset():
            return MCPToolset(InitializationRetryClient(server), id="selection")

        return create_toolset

    async def model(messages, info):
        expected = {"selected", "unselected"} if tools is None else set(tools)
        assert {tool.name for tool in info.function_tools} == expected
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(MCPServer(id="selection", prepare=prepare, tools=tools),),
    )
    assert (await executable.run("go", bindings=RunBindings.embedded())).output_or_raise() == "done"


async def test_project_failure_propagates_and_cleans_up_entered_toolset():
    client = InitializationRetryClient(FastMCP("projection"))

    async def prepare(ctx):
        async def create_toolset():
            return MCPToolset(client, id="projection")

        return create_toolset

    def project(toolset):
        assert client.is_connected()
        raise ValueError("Host projection failed")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(lambda messages, info: "unused"),
        capabilities=(MCPServer(id="projection", prepare=prepare, project=project, on_error="skip"),),
    )
    with pytest.raises(ValueError, match="Host projection failed"):
        await executable.run("go", bindings=RunBindings.embedded())
    assert not client.is_connected()
