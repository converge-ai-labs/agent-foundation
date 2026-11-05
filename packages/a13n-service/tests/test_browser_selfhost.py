"""Allocation recovery preserves known non-dispatch versus unknown effects."""

import json
from unittest.mock import AsyncMock

import pytest
from a13n_service.providers.browser import runtime
from a13n_service.providers.browser.definition import BrowserProviderError, LiveBrowser
from a13n_service.providers.browser.native import Native
from a13n_service.providers.browser.selfhost import Recipe, SelfHosted

pytestmark = pytest.mark.anyio


async def test_create_can_retry_known_pre_dispatch_failure_but_not_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(SelfHosted, "__abstractmethods__", frozenset())
    provider = SelfHosted(tmp_path, "test")
    provider.provision = AsyncMock(side_effect=BrowserProviderError("image_missing", certainty="not_dispatched"))
    provider.launch = AsyncMock(return_value=LiveBrowser({}))
    provider.observe = AsyncMock(return_value=None)
    with pytest.raises(BrowserProviderError):
        await provider.create("browser_retry", Recipe())
    assert json.loads((tmp_path / "browser_retry/allocation.json").read_text()) == {}
    provider.provision.side_effect = None
    await provider.create("browser_retry", Recipe())
    assert provider.launch.await_count == 1

    provider.provision.side_effect = BrowserProviderError("transport_lost")
    with pytest.raises(BrowserProviderError):
        await provider.create("browser_unknown", Recipe())
    provider.provision.reset_mock()
    with pytest.raises(BrowserProviderError) as failed:
        await provider.create("browser_unknown", Recipe())
    assert failed.value.code == "allocation_create_unresolved"
    provider.provision.assert_not_awaited()
    await provider.delete("browser_unknown", None)
    with pytest.raises(BrowserProviderError) as deleted:
        await provider.create("browser_unknown", Recipe())
    assert deleted.value.code == "allocation_deleted"


async def test_native_unpublished_runtime_cannot_be_observed_stopped_or_restarted(tmp_path, monkeypatch):
    provider = Native(tmp_path, "chromium")
    monkeypatch.setattr(runtime, "group_alive", lambda pid: pid == 4567)
    async with provider.allocation("browser_pending") as allocation:
        allocation.record = {"backend": "native", "recipe": Recipe().model_dump(), "generation": 1}
        await provider.provision(allocation)
        from pathlib import Path

        allocation.record.update(
            boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip(), pid=4567, started="123"
        )
        await allocation.save()
        with pytest.raises(BrowserProviderError) as pending:
            await provider.observe(allocation)
        assert pending.value.code == "native_start_pending"
        with pytest.raises(BrowserProviderError) as refused:
            await provider.launch(allocation)
        assert refused.value.code == "native_writer_unconfirmed"
        assert refused.value.certainty == "not_dispatched"


async def test_native_cancel_waits_for_spawn_identity_under_allocation_lock(tmp_path, monkeypatch):
    import asyncio

    provider = Native(tmp_path, "chromium")
    async with provider.allocation("browser_cancel") as allocation:
        allocation.record = {"backend": "native", "recipe": Recipe().model_dump(), "generation": 1}
        await provider.provision(allocation)
        await allocation.save()
    provider.observe = AsyncMock(return_value=LiveBrowser({}, status="stopped"))
    original_spawn = asyncio.create_subprocess_exec
    spawned, proceed = asyncio.Event(), asyncio.Event()
    child = None

    async def paused_spawn(*args, **kwargs):
        nonlocal child
        import sys

        child = await original_spawn(
            sys.executable,
            "-c",
            "import os,sys,time; os.read(int(sys.argv[1]),1); time.sleep(60)",
            str(kwargs["pass_fds"][0]),
            pass_fds=kwargs["pass_fds"],
            start_new_session=True,
        )
        spawned.set()
        await proceed.wait()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", paused_spawn)
    start = asyncio.create_task(provider.start("browser_cancel", {}, generation=2))
    try:
        await spawned.wait()
        start.cancel()
        await asyncio.sleep(0)
        acquired = asyncio.Event()

        async def contender():
            async with provider.allocation("browser_cancel"):
                acquired.set()

        waiter = asyncio.create_task(contender())
        await asyncio.sleep(0.15)
        assert not acquired.is_set()
        proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await start
        await asyncio.wait_for(waiter, 2)
        record = json.loads((tmp_path / "browser_cancel/allocation.json").read_text())
        assert record["pid"] == child.pid
        assert record["started"] == runtime.process_identity(child.pid)
    finally:
        proceed.set()
        if child and child.returncode is None:
            child.terminate()
            await child.wait()


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("failure", ["spawn", "save"])
async def test_native_start_spawn_and_record_failure(tmp_path, monkeypatch, failure, cancel):
    import asyncio

    from a13n_service.providers.browser.selfhost import Allocation

    provider = Native(tmp_path, "chromium")
    async with provider.allocation("browser_failure") as allocation:
        allocation.record = {"backend": "native", "recipe": Recipe().model_dump(), "generation": 1}
        await provider.provision(allocation)
        await allocation.save()
    provider.observe = AsyncMock(return_value=LiveBrowser({}, status="stopped"))
    original_spawn = asyncio.create_subprocess_exec
    original_save = Allocation.save
    child = None
    paused, proceed = asyncio.Event(), asyncio.Event()
    failed = False

    async def spawn(*args, **kwargs):
        nonlocal child
        paused.set()
        await proceed.wait()
        if failure == "spawn":
            raise OSError("spawn failed")
        import sys

        child = await original_spawn(
            sys.executable,
            "-c",
            "import os,sys; os.read(int(sys.argv[1]),1)",
            str(kwargs["pass_fds"][0]),
            pass_fds=kwargs["pass_fds"],
            start_new_session=True,
        )
        return child

    async def save(allocation):
        nonlocal failed
        if failure == "save" and "pid" in allocation.record and not failed:
            failed = True
            raise OSError("save failed")
        await original_save(allocation)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(Allocation, "save", save)
    try:
        task = asyncio.create_task(provider.start("browser_failure", {}, generation=2))
        await paused.wait()
        if cancel:
            task.cancel()
            await asyncio.sleep(0)
        proceed.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError) as error:
                await task
            assert isinstance(error.value.__cause__, OSError)
        else:
            with pytest.raises(OSError, match=f"{failure} failed"):
                await task
        if child:
            assert child.returncode is not None
            assert not runtime.group_alive(child.pid)
        async with provider.allocation("browser_failure"):
            pass
    finally:
        if child and child.returncode is None:
            child.terminate()
            await child.wait()


async def test_download_size_classification_for_real_provider_readers(tmp_path):
    import io
    import tarfile
    from types import SimpleNamespace
    from unittest.mock import Mock

    from a13n_service.providers.browser.docker import Configuration, Docker
    from a13n_service.providers.browser.selfhost import Allocation

    allocation = Allocation("browser_files", tmp_path, {})
    directory = tmp_path / "data" / "downloads"
    directory.mkdir(parents=True)
    (directory / "big").write_bytes(b"12345")
    native = Native(tmp_path, "chromium")
    with pytest.raises(BrowserProviderError) as error:
        await native.read_download(allocation, "big", 4)
    assert error.value.code == "download_too_large"

    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        member = tarfile.TarInfo("big")
        member.size = 5
        archive.addfile(member, io.BytesIO(b"12345"))
    container = SimpleNamespace(get_archive=Mock())
    docker = Docker(tmp_path, None, Configuration())
    docker.container = AsyncMock(return_value=container)
    for metadata in ({"size": 5}, {"size": 4}):
        container.get_archive.return_value = (iter_chunks(data.getvalue()), metadata)
        with pytest.raises(BrowserProviderError) as error:
            await docker.read_download(allocation, "big", 4)
        assert error.value.code == "download_too_large"
    container.get_archive.return_value = (iter_chunks(data.getvalue()), {"size": 5, "linkTarget": "x"})
    with pytest.raises(BrowserProviderError) as error:
        await docker.read_download(allocation, "big", 4)
    assert error.value.code == "download_size_or_kind"


def iter_chunks(data):
    yield data


async def test_native_unconfirmed_cleanup_keeps_durable_fence(tmp_path, monkeypatch):
    import asyncio
    import os
    import signal
    import sys

    from a13n_service.providers.browser import native
    from a13n_service.providers.browser.selfhost import Allocation

    provider = Native(tmp_path, "chromium")
    async with provider.allocation("browser_uncertain") as allocation:
        allocation.record = {"backend": "native", "recipe": Recipe().model_dump(), "generation": 1}
        await provider.provision(allocation)
        await allocation.save()
    original_observe = provider.observe
    provider.observe = AsyncMock(return_value=LiveBrowser({}, status="stopped"))
    original_spawn = asyncio.create_subprocess_exec
    original_save = Allocation.save
    original_killpg = os.killpg
    child = None

    async def spawn(*args, **kwargs):
        nonlocal child
        child = await original_spawn(
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert await child.stdout.readline() == b"ready\n"
        return child

    async def save(allocation):
        if "pid" in allocation.record:
            raise OSError("save failed")
        await original_save(allocation)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(Allocation, "save", save)
    monkeypatch.setattr(native, "_CLEANUP_TIMEOUT", 0.1)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: None)
    try:
        with pytest.raises(TimeoutError):
            await provider.start("browser_uncertain", {}, generation=2)
        assert child.returncode is None
        provider.observe = original_observe
        record = json.loads((tmp_path / "browser_uncertain/allocation.json").read_text())
        assert record["launch_unresolved"]["version"] == 1
        with pytest.raises(BrowserProviderError, match="Browser provider operation failed") as error:
            await provider.inspect("browser_uncertain", None)
        assert error.value.code == "native_writer_unconfirmed"
        with pytest.raises(BrowserProviderError):
            await provider.start("browser_uncertain", {}, generation=3)
        with pytest.raises(BrowserProviderError):
            await provider.delete("browser_uncertain", None)
        assert (tmp_path / "browser_uncertain/data").exists()
    finally:
        if child and child.returncode is None:
            original_killpg(child.pid, signal.SIGKILL)
            await child.wait()


@pytest.mark.parametrize(
    ("code", "status", "same_id", "stopped"),
    [
        (409, "exited", True, True),
        (409, "created", True, True),
        (409, "dead", True, True),
        (409, "running", True, False),
        (409, "paused", True, False),
        (409, None, True, False),
        (409, "exited", False, False),
        (500, "exited", True, False),
    ],
)
async def test_docker_observe_exec_race(tmp_path, code, status, same_id, stopped):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from a13n_service.providers.browser.docker import LABEL, Configuration, Docker
    from a13n_service.providers.browser.selfhost import Allocation
    from docker.errors import APIError, NotFound
    from requests import Response

    response = Response()
    response.status_code = code
    error = APIError("exec failed", response=response)
    initial = SimpleNamespace(
        id="container", labels={LABEL: "browser_race"}, status="running", exec_run=Mock(side_effect=error)
    )
    current = (
        SimpleNamespace(id="container" if same_id else "other", labels={LABEL: "browser_race"}, status=status)
        if status is not None
        else NotFound("missing")
    )
    client = SimpleNamespace(
        info=Mock(return_value={"ID": "daemon"}), containers=SimpleNamespace(get=Mock(side_effect=[initial, current]))
    )
    provider = Docker(tmp_path, client, Configuration())
    allocation = Allocation(
        "browser_race", tmp_path, {"daemon": "daemon", "container_id": "container", "runtime_id": "runtime"}
    )
    if stopped:
        live = await provider.observe(allocation)
        assert live.status == "stopped" and live.runtime_id == "runtime"
        assert live.state["container_id"] == "container"
    elif not same_id:
        with pytest.raises(BrowserProviderError) as raised:
            await provider.observe(allocation)
        assert raised.value.code == "docker_identity_changed"
    else:
        with pytest.raises(APIError) as raised:
            await provider.observe(allocation)
        assert raised.value is error
    assert client.containers.get.call_count == (1 if code != 409 else 2)


async def test_stop_fences_runtime_and_requires_confirmed_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(SelfHosted, "__abstractmethods__", frozenset())
    provider = SelfHosted(tmp_path, "test")
    ready = LiveBrowser({}, runtime_id="current")
    stopped = LiveBrowser({}, status="stopped", runtime_id="current")
    provider.observe = AsyncMock(return_value=ready)
    provider.halt = AsyncMock()
    with pytest.raises(BrowserProviderError) as changed:
        await provider.stop("browser_stop", {"runtime_id": "old"})
    assert changed.value.code == "runtime_changed"
    provider.halt.assert_not_awaited()
    with pytest.raises(BrowserProviderError) as unconfirmed:
        await provider.stop("browser_stop", {"runtime_id": "current"})
    assert unconfirmed.value.code == "runtime_stop_unconfirmed"
    provider.observe.side_effect = [ready, stopped]
    assert await provider.stop("browser_stop", {"runtime_id": "current"}) == stopped


@pytest.fixture
def starting_runtime(tmp_path):
    import asyncio
    from types import SimpleNamespace

    instance = object.__new__(runtime.Runtime)
    instance.root = tmp_path
    instance.stop = asyncio.Event()
    instance.cdp_url = None
    instance.guest = SimpleNamespace(call=AsyncMock(return_value={"result": {"generation": "viewer"}}))
    (tmp_path / "desktop").mkdir()
    (tmp_path / "desktop" / "control.sock").touch()
    endpoint = tmp_path / "DevToolsActivePort"
    endpoint.write_text("1234\n/devtools/browser/test\n")
    return instance, endpoint, SimpleNamespace(returncode=None)


@pytest.mark.parametrize("failure", [ConnectionRefusedError, FileNotFoundError])
async def test_runtime_waits_for_viewer_response_not_socket_existence(starting_runtime, failure):
    instance, endpoint, child = starting_runtime
    description = {"result": {"generation": "viewer"}}
    instance.guest.call.side_effect = [failure(), description]
    assert await instance.wait_ready(endpoint, (child,)) == ("1234", "/devtools/browser/test", description)
    assert instance.guest.call.await_count == 2
    assert instance.cdp_url == "ws://127.0.0.1:1234/devtools/browser/test"
    instance.guest.call.assert_awaited_with(instance.root / "desktop", {"protocol_version": 2, "operation": "describe"})


@pytest.mark.parametrize("failure", ["child_exit", "stop", "timeout", "viewer_error", "permission"])
async def test_runtime_readiness_remains_bounded_and_propagates_failures(starting_runtime, monkeypatch, failure):
    import asyncio

    instance, endpoint, child = starting_runtime
    original_timeout = asyncio.timeout
    budgets = []

    def timeout(seconds):
        budgets.append(seconds)
        return original_timeout(0.02 if failure == "timeout" else seconds)

    monkeypatch.setattr(runtime.asyncio, "timeout", timeout)

    async def describe(*args):
        if failure == "child_exit":
            child.returncode = 1
        elif failure == "stop":
            instance.stop.set()
        elif failure == "viewer_error":
            return {"error": "operation_unconfirmed"}
        elif failure == "permission":
            raise PermissionError("denied")
        raise ConnectionRefusedError()

    instance.guest.call.side_effect = describe
    error = TimeoutError if failure == "timeout" else PermissionError if failure == "permission" else RuntimeError
    with pytest.raises(error):
        await instance.wait_ready(endpoint, (child,))
    assert budgets == [30]
    assert instance.guest.call.await_count == 1


async def test_runtime_child_cleanup_has_one_shared_deadline(monkeypatch):
    import asyncio

    events = []

    class Child:
        def __init__(self, name, stuck=False):
            self.name, self.stuck, self.returncode = name, stuck, None

        def terminate(self):
            events.append((self.name, "terminate"))
            if not self.stuck:
                self.returncode = 0

        def kill(self):
            events.append((self.name, "kill"))
            self.returncode = -9

        async def wait(self):
            while self.returncode is None:
                await asyncio.sleep(0.001)
            return self.returncode

    original_timeout = asyncio.timeout
    budgets = []

    def timeout(seconds):
        budgets.append(seconds)
        return original_timeout(0.01)

    monkeypatch.setattr(runtime.asyncio, "timeout", timeout)
    instance = object.__new__(runtime.Runtime)
    instance.children = [Child("display"), Child("browser", stuck=True)]
    await instance.close_children()
    assert budgets == [5]
    assert events == [("browser", "terminate"), ("browser", "kill"), ("display", "kill")]


@pytest.mark.parametrize("failure", [OSError, TimeoutError])
async def test_runtime_cdp_failure_falls_back_to_child_cleanup(monkeypatch, failure):
    class BrokenConnection:
        async def __aenter__(self):
            raise failure()

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(runtime, "connect", lambda *args, **kwargs: BrokenConnection())
    instance = object.__new__(runtime.Runtime)
    instance.chromium = type("Process", (), {"returncode": None})()
    instance.cdp_url = "ws://invalid"
    await instance.close_chromium()


@pytest.mark.parametrize(
    "policy,cached,pulls",
    [("always", True, True), ("if_missing", True, False), ("if_missing", False, True), ("never", True, False)],
)
async def test_docker_image_policy_for_new_allocation(tmp_path, policy, cached, pulls):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from a13n_service.providers.browser.docker import Configuration, Docker
    from a13n_service.providers.browser.selfhost import Allocation
    from docker.errors import ImageNotFound, NotFound

    old = SimpleNamespace(id="old-image")
    new = SimpleNamespace(id="new-image")
    container = SimpleNamespace(id="container")
    images = SimpleNamespace(
        get=Mock(return_value=old) if cached else Mock(side_effect=ImageNotFound("missing")),
        pull=Mock(return_value=new),
    )
    containers = SimpleNamespace(get=Mock(side_effect=NotFound("missing")), create=Mock(return_value=container))
    client = SimpleNamespace(info=Mock(return_value={"ID": "daemon"}), images=images, containers=containers)
    provider = Docker(tmp_path, client, Configuration(image="example:tag", pull_policy=policy))
    allocation = Allocation("browser_image", tmp_path, {"recipe": {"width": 800, "height": 600}})
    await provider.provision(allocation)
    assert images.pull.call_count == int(pulls)
    assert containers.create.call_args.args[0] == ("new-image" if pulls else "old-image")
    assert allocation.record["container_id"] == "container"


async def test_docker_retained_container_skips_pull_and_create(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from a13n_service.providers.browser.docker import LABEL, Configuration, Docker
    from a13n_service.providers.browser.selfhost import Allocation

    container = SimpleNamespace(id="retained", labels={LABEL: "browser_retained"})
    client = SimpleNamespace(info=Mock(return_value={"ID": "daemon"}), images=Mock(), containers=Mock())
    client.containers.get.return_value = container
    provider = Docker(tmp_path, client, Configuration())
    allocation = Allocation("browser_retained", tmp_path, {"container_id": "retained"})
    await provider.provision(allocation)
    client.images.pull.assert_not_called()
    client.containers.create.assert_not_called()


async def test_docker_pull_failure_does_not_create(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from a13n_service.providers.browser.docker import Configuration, Docker
    from a13n_service.providers.browser.selfhost import Allocation
    from docker.errors import NotFound

    client = SimpleNamespace(info=Mock(return_value={"ID": "daemon"}), images=Mock(), containers=Mock())
    client.containers.get.side_effect = NotFound("missing")
    client.images.pull.side_effect = RuntimeError("registry unavailable")
    provider = Docker(tmp_path, client, Configuration())
    allocation = Allocation("browser_failure", tmp_path, {"recipe": {"width": 800, "height": 600}})
    with pytest.raises(RuntimeError, match="registry unavailable"):
        await provider.provision(allocation)
    client.containers.create.assert_not_called()
