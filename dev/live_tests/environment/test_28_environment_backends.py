"""Cases 3/20/21/22 across five real Environment backends and their capability limits."""

import logging
import os
from pathlib import Path
from uuid import uuid4

import pytest

from ..infrastructure.round_two_lab import REPOSITORY, open_lab
from .environment_backends import BACKENDS, REMOTE, EnvironmentBackend, provider_configuration
from .service_cases import (
    assert_access_policy,
    assert_managed_continuity,
    assert_template_preparation,
    execute,
    view,
    write,
)

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", params=BACKENDS)
async def environment_backend(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in with --live-environments for real envd, Docker and configured E2B")
    kind, binary, settings = request.param, None, None
    if kind in {"local_envd", *REMOTE}:
        binary = Path(os.environ.get("A13N_ENVD_TEST_BINARY", REPOSITORY / "target/debug/a13n-envd")).resolve()
        if not binary.is_file():
            pytest.fail("Build a13n-envd or set A13N_ENVD_TEST_BINARY before enabling the Environment matrix")
    if kind == "e2b":
        from ..providers.provider_config import load_provider_settings

        settings = load_provider_settings().environment
        if settings is None:
            pytest.skip("Configure the optional E2B environment section to run its matrix")
    async with open_lab(suite="management", websocket_envd=kind == "websocket_envd") as lab:
        backend = EnvironmentBackend(lab, kind, binary, settings)
        yield backend


async def test_environment_backend_tools_and_access(environment_backend):
    backend, journey = environment_backend, environment_backend.journey
    for access in ("read_only", "read_write", "full"):
        async with backend.target() as target:
            environment = await target.allocate(access=access)
            await assert_access_policy(
                journey,
                environment,
                access,
                root=None if backend.kind in {"e2b", "docker"} else target.root,
                read_text=target.read_text,
            )
            logger.info(
                "Environment backend=%s access=%s passed real operation/effect assertions", backend.kind, access
            )


async def test_environment_backend_templates_and_preparation(environment_backend):
    backend, journey = environment_backend, environment_backend.journey
    live = journey.live
    if backend.kind in REMOTE:
        async with backend.target() as target:
            denied = await journey.post(
                journey.base + "/environment-templates",
                {"name": "Unsupported remote template", **target.template_config},
                expected=422,
            )
            assert denied["error"]["code"] == "environment_invalid", denied
            assert (await live.collection(journey.base + "/environment-templates")) == []
            assert target.process.returncode is None
        return
    for preparation in ("on_run", "on_use"):
        async with backend.target() as target:
            template = await target.template(access="read_write", preparation=preparation)
            revised = {
                **target.template_config,
                "access": "full",
                "preparation": preparation,
                "configuration": provider_configuration(
                    backend.kind, target.root.parent / "version-two", backend.settings
                ),
            }
            roots = None if backend.kind in {"e2b", "docker"} else (target.root, target.root.parent / "version-two")
            await assert_template_preparation(
                journey, template, revised, preparation=preparation, initial_access="read_write", roots=roots
            )
            logger.info(
                "Environment backend=%s preparation=%s retained exact revisions and prepared once",
                backend.kind,
                preparation,
            )


async def test_environment_backend_lifecycle_and_continuity(environment_backend):
    backend, journey = environment_backend, environment_backend.journey
    live = journey.live
    async with backend.target() as target:
        environment = await target.allocate()
        if backend.kind in {"docker", "e2b"}:
            await assert_managed_continuity(journey, environment, preserves_files=False)
            return
        marker = "CONTINUITY_" + uuid4().hex
        original, _ = await execute(journey, environment, [write("/workspace/proof.txt", marker)])
        path = f"/api/v1/environments/{environment['id']}"
        result, _ = await execute(journey, environment, [view("/workspace/proof.txt")])
        assert marker in result["output_text"] and result["environment_id"] == original["environment_id"]
        assert await live.run(original["id"]) == original
        for action in ("stop", "delete"):
            denied = await journey.post(path + "/" + action, {}, expected=409 if backend.kind in REMOTE else 422)
            assert denied["error"]["code"] == (
                "environment_busy" if backend.kind in REMOTE else "environment_invalid"
            ), denied
        result, _ = await execute(journey, environment, [view("/workspace/proof.txt")])
        assert marker in result["output_text"]
        if target.process is not None:
            assert target.process.returncode is None, "Service stopped an externally owned daemon"
        assert target.root.joinpath("proof.txt").read_text() == marker
        logger.info("Environment backend=%s continuity and lifecycle capability boundaries passed", backend.kind)
