"""Official Daytona wire shapes, observation fencing and legacy compatibility."""

import asyncio
import base64
import io
import json
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_harness.environment.computer import ComputerClick, ComputerPoint, ComputerScroll, ComputerTypeText
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.providers.environment.daytona.computer import DaytonaComputer
from a13n_harness.providers.environment.daytona.provider import DAYTONA
from a13n_harness.providers.environment.daytona.shared import DaytonaEnvironmentConfiguration
from a13n_harness.providers.environment.native.commands import NativeCommands
from a13n_harness.providers.environment.native.configuration import CommandConfiguration
from a13n_harness.providers.environment.native.http import NativeHTTP
from PIL import Image

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def desktop():
    image = io.BytesIO()
    Image.new("RGB", (1280, 800), "white").save(image, format="PNG")
    calls = []
    state = {"fail": False, "identity": "boot-id\n123 date"}

    async def request(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "POST":
            if state["fail"]:
                raise httpx2.ReadTimeout("response lost")
            return httpx2.Response(200, json={"success": True, "x": 0, "y": 0})
        if request.url.path.endswith("display/info"):
            return httpx2.Response(
                200, json={"displays": [{"id": 0, "width": 1280, "height": 800, "x": 0, "y": 0, "isActive": True}]}
            )
        if request.url.path.endswith("screenshot"):
            return httpx2.Response(200, json={"screenshot": base64.b64encode(image.getvalue()).decode()})
        raise AssertionError(request.url.path)

    async def execute(argv, timeout):
        return state["identity"]

    commands = NativeCommands(execute, DaytonaEnvironmentConfiguration(desktop=True), "generation-test", "exec-test")
    transport = NativeHTTP("daytona", "https://toolbox.fixture/", "unused", 3)
    await transport.close()
    transport.client = httpx2.AsyncClient(base_url="https://toolbox.fixture/", transport=httpx2.MockTransport(request))
    computer = DaytonaComputer(commands, transport, "sandbox-test")
    yield computer, calls, state
    commands.closed = True
    await transport.close()


def test_legacy_fingerprint_and_opt_in_capability():
    ordinary = DaytonaEnvironmentConfiguration()

    class LegacyDaytona(CommandConfiguration):
        root: str = "/home/daytona"
        snapshot: str | None = None

    assert ordinary.fingerprint == LegacyDaytona().fingerprint
    assert "computer" not in DAYTONA.describe_environment(ordinary).operation_families
    enabled = DaytonaEnvironmentConfiguration(desktop=True)
    assert enabled.fingerprint != ordinary.fingerprint
    description = DAYTONA.describe_environment(enabled)
    assert "computer" in description.operation_families
    assert EnvironmentAction.COMPUTER_ZOOM not in description.permissions.operations


async def test_scaled_coordinates_and_unicode_payload(desktop):
    computer, calls, _ = desktop
    screen = await computer.observe(max_dimension=640)
    assert (screen.observation.width, screen.observation.height) == (640, 400)
    await computer.execute(ComputerClick(observation=screen.observation, point=ComputerPoint(x=0, y=399)))
    assert calls[-1] == (
        "POST",
        "/sandbox-test/computeruse/mouse/click",
        {"x": 0, "y": 798, "button": "left", "double": False},
    )
    text = '中文输入\n第二行 "quoted"'
    result = await computer.execute(ComputerTypeText(text=text))
    payloads = [(path.rsplit("/", 1)[1], body) for method, path, body in calls if method == "POST"][1:]
    reconstructed = "".join("\n" if kind == "hotkey" else body["text"] for kind, body in payloads)
    assert reconstructed == text
    assert all(len(body["text"]) == 1 or body["text"].isascii() for kind, body in payloads if kind == "type")
    assert result.input_cleanup_complete


async def test_stale_and_invalid_inputs_never_dispatch(desktop):
    computer, calls, state = desktop
    screen = await computer.observe()
    requests = [
        ComputerClick(
            observation=screen.observation.model_copy(update={"execution_id": "other"}), point=ComputerPoint(x=1, y=1)
        ),
        ComputerClick(observation=screen.observation, point=ComputerPoint(x=1280, y=1)),
        ComputerScroll(observation=screen.observation, point=ComputerPoint(x=1, y=1), delta_y=1, unit="pixels"),
    ]
    for request in requests:
        with pytest.raises(EnvironmentError) as error:
            await computer.execute(request)
        assert error.value.details["dispatch_stage"] == "pre_dispatch"
    state["identity"] = "boot-id\n124 later"
    with pytest.raises(EnvironmentError, match="stale"):
        await computer.execute(ComputerClick(observation=screen.observation, point=ComputerPoint(x=1, y=1)))
    assert not any(method == "POST" for method, _, _ in calls)
    assert not computer.blocked


async def test_lost_input_response_blocks_without_retry(desktop):
    computer, calls, state = desktop
    state["fail"] = True
    with pytest.raises(EnvironmentProviderError):
        await computer.execute(ComputerTypeText(text="do not replay"))
    with pytest.raises(EnvironmentError, match="Reconcile"):
        await computer.execute(ComputerTypeText(text="again"))
    assert len(calls) == 1
    assert not (await computer.describe()).input_ready


@pytest.mark.parametrize("reply", [{}, {"success": None}, {"success": False}, {"success": "true"}])
async def test_unconfirmed_scroll_blocks_without_retry(desktop, reply):
    computer, _, _ = desktop
    screen = await computer.observe()
    computer.toolbox.request = AsyncMock(return_value=reply)
    computer.geometry = AsyncMock(return_value=computer.observations[screen.observation.observation_id].geometry)
    with pytest.raises(EnvironmentError, match="not confirmed"):
        await computer.execute(
            ComputerScroll(observation=screen.observation, point=ComputerPoint(x=1, y=1), delta_y=1, unit="steps")
        )
    with pytest.raises(EnvironmentError, match="Reconcile"):
        await computer.execute(ComputerTypeText(text="do not replay"))
    computer.toolbox.request.assert_awaited_once()


async def test_cancellation_fences_input_and_closed_scope(desktop):
    computer, _, _ = desktop
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def wait(request):
        entered.set()
        await finish.wait()

    computer.dispatch = AsyncMock(side_effect=wait)
    task = asyncio.create_task(computer.execute(ComputerTypeText(text="text")))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert computer.lock.locked()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert computer.blocked
    computer.commands.closed = True
    with pytest.raises(EnvironmentError, match="closed"):
        await computer.describe()


async def test_failed_drag_attempts_release_and_blocks_further_input(desktop):
    from a13n_harness.environment.computer import ComputerDrag

    computer, _, _ = desktop
    screen = await computer.observe()
    posted = []

    async def post(operation, body):
        posted.append((operation, body))
        if operation == "mouse/move":
            raise EnvironmentError("Lost movement response.", code="environment_unavailable")

    computer.post = post
    with pytest.raises(EnvironmentError, match="Lost movement"):
        await computer.execute(
            ComputerDrag(
                observation=screen.observation,
                start=ComputerPoint(x=1, y=1),
                end=ComputerPoint(x=100, y=100),
                duration_ms=100,
            )
        )
    assert posted[-1] == ("mouse/up", {"button": "left"})
    assert computer.blocked


async def test_compound_timeout_cleans_up_and_does_not_replay(desktop):
    from a13n_harness.environment.computer import ComputerDrag

    computer, _, _ = desktop
    computer.commands.configuration = DaytonaEnvironmentConfiguration(desktop=True, request_timeout_seconds=0.02)
    screen = await computer.observe()
    posted = []

    async def post(operation, body):
        posted.append(operation)
        if operation == "mouse/down":
            await asyncio.sleep(10)

    computer.post = post
    with pytest.raises(TimeoutError):
        await computer.execute(
            ComputerDrag(
                observation=screen.observation,
                start=ComputerPoint(x=1, y=1),
                end=ComputerPoint(x=100, y=100),
            )
        )
    assert posted == ["mouse/down", "mouse/up"]
    assert computer.blocked


async def test_close_waits_for_in_flight_input(desktop):
    computer, _, _ = desktop
    entered, finish = asyncio.Event(), asyncio.Event()

    async def dispatch(request):
        entered.set()
        await finish.wait()

    computer.dispatch = dispatch
    action = asyncio.create_task(computer.execute(ComputerTypeText(text="pending")))
    await entered.wait()
    closing = asyncio.create_task(computer.close())
    await asyncio.sleep(0)
    assert not closing.done()
    finish.set()
    await action
    await closing
    assert computer.commands.closed
    assert not computer.observations
