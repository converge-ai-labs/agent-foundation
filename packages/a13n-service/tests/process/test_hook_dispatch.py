import anyio
import pytest
from a13n_service.app import create_app
from a13n_service.settings import ProcessRole


@pytest.mark.anyio
@pytest.mark.parametrize("role", tuple(ProcessRole))
async def test_hook_dispatch_runs_only_in_control_capable_roles(local_settings, tmp_path, monkeypatch, role):
    started = anyio.Event()
    stopped = anyio.Event()

    async def run(dispatcher):
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            stopped.set()

    monkeypatch.setattr("a13n_service.hooks.dispatcher.HookDispatcher.run", run)
    app = create_app(local_settings(tmp_path / role.value, role=role))
    async with app.router.lifespan_context(app):
        if role in {ProcessRole.all, ProcessRole.control}:
            with anyio.fail_after(10):
                await started.wait()
        else:
            assert not started.is_set()
    assert stopped.is_set() == (role in {ProcessRole.all, ProcessRole.control})
