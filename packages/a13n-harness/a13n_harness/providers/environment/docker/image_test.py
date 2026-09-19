"""Bounded compatibility test using the native Docker operation implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from ..commands import ArgvCommand, CommandRequest, ShellCommand
from ..retention import EnvironmentOutputPolicy
from .configuration import DockerEnvironmentConfiguration
from .provider import DockerEnvironment, resolve_image
from .runtime import DockerProviderRuntime, DockerSDKEngine


@dataclass(frozen=True)
class DockerImageTestResult:
    image_id: str
    image_source: Literal["local", "pulled"]
    checks: tuple[str, ...]


class DockerImageTestFailure(RuntimeError):
    def __init__(self, image_id: str, image_source: Literal["local", "pulled"], message: str) -> None:
        self.image_id = image_id
        self.image_source: Literal["local", "pulled"] = image_source
        super().__init__(message)


async def test_docker_image(
    engine: DockerSDKEngine, configuration: DockerEnvironmentConfiguration, *, timeout_seconds: float = 120
) -> DockerImageTestResult:
    """Check a draft image without its init script or business mounts."""
    image_id: str | None = None
    source: Literal["local", "pulled"] = "local"
    try:
        async with asyncio.timeout(timeout_seconds):
            try:
                if not await asyncio.to_thread(engine.client.ping):
                    raise RuntimeError("Docker Engine ping returned false")
            except Exception as error:
                raise RuntimeError(f"Docker Engine is unavailable or permission denied: {error}") from error
            try:
                image, source = await asyncio.to_thread(resolve_image, engine.client, configuration.image)
            except Exception as error:
                raise RuntimeError(
                    f"Image {configuration.image} could not be resolved locally or pulled: {error}"
                ) from error
            if not isinstance(image.id, str) or not image.id:
                raise RuntimeError("Docker did not return an image ID")
            image_id = image.id
            try:
                checks = await _exercise_image(engine, configuration, image_id)
            except Exception as error:
                raise DockerImageTestFailure(image.id, source, str(error)) from error
            return DockerImageTestResult(image.id, source, checks)

    except TimeoutError as error:
        message = f"Image test timed out after {timeout_seconds:g} seconds"
        if image_id is not None:
            raise DockerImageTestFailure(image_id, source, message) from error
        raise RuntimeError(message) from error


async def _exercise_image(
    engine: DockerSDKEngine, configuration: DockerEnvironmentConfiguration, image_id: str
) -> tuple[str, ...]:
    test_config = configuration.model_copy(update={"image": image_id, "mounts": (), "init_script": None})
    environment = DockerEnvironment(test_config, f"envtest_{uuid4().hex}", None, DockerProviderRuntime(engine))
    try:
        try:
            await environment.prepare()
        except Exception as error:
            reason = await _preparation_failure(engine, environment, configuration, error)
            raise RuntimeError(reason) from error
        assert environment.commands is not None and environment.processes is not None
        try:
            await environment.commands.execute([configuration.shell, "-c", "exit 0"])
        except Exception as error:
            raise RuntimeError(f"Configured shell {configuration.shell} failed: {error}") from error
        checks = ["python", "shell", "runtime directories"]
        files = environment.operations.files
        assert files is not None
        await files.write_text("/workspace/.a13n-image-test", "ready", mode="create")
        contents = await files.read_bytes("/workspace/.a13n-image-test")
        if contents != b"ready":
            raise RuntimeError("Guest file round trip returned unexpected bytes")
        await files.remove("/workspace/.a13n-image-test")
        checks.append("files")
        policy = EnvironmentOutputPolicy(max_inline_bytes=1024, max_output_bytes=1024, overflow="fail")
        output = await environment.processes.exec(
            CommandRequest(command=ShellCommand(profile_id="default", script="printf image-test"), output_policy=policy)
        )
        if output.status.exit_code != 0 or output.output.stdout.inline != b"image-test":
            raise RuntimeError("Guest shell output check failed")
        checks.extend(("commands", "outputs"))
        started = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=configuration.python, arguments=("-I", "-c", "import time; time.sleep(30)")
                ),
                output_policy=policy,
            )
        )
        handle = started.process.handle
        try:
            running = await environment.processes.inspect(handle)
            try:
                async with asyncio.timeout(2):
                    while running.status.phase == "starting":
                        await asyncio.sleep(0.05)
                        running = await environment.processes.inspect(handle)
            except TimeoutError as error:
                raise RuntimeError("Guest process never reached running state") from error
            if running.status.phase != "running":
                raise RuntimeError(f"Guest process exited before control check: {running.status.phase}")
            await environment.processes.kill(handle)
            stopped = await environment.processes.wait(handle, condition="initial_terminal", timeout_seconds=5)
            if stopped.status.phase != "exited" or stopped.status.exit_code not in {137, -9}:
                raise RuntimeError(f"Guest process control did not confirm kill: {stopped.status}")
        finally:
            await environment.processes.release(handle)
        checks.append("process control")
        return tuple(checks)
    finally:

        async def cleanup() -> None:
            try:
                await environment.close()
            finally:
                cleanup_environment = DockerEnvironment(
                    test_config,
                    environment.environment_id,
                    environment.dump_state(),
                    DockerProviderRuntime(engine),
                )
                await cleanup_environment.destroy()

        task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise


async def _preparation_failure(
    engine: DockerSDKEngine,
    environment: DockerEnvironment,
    configuration: DockerEnvironmentConfiguration,
    error: Exception,
) -> str:
    try:
        if environment.target is not None:
            container = await asyncio.to_thread(engine.client.containers.get, environment.target.container_id)
            await asyncio.to_thread(container.reload)
            if container.status == "running":
                check = await asyncio.to_thread(
                    container.exec_run,
                    [configuration.shell, "-c", "test -w /workspace && test -w /tmp/a13n"],
                    user=configuration.user or "",
                )
                if check.exit_code == 1:
                    user = configuration.user or "the image default user"
                    return f"Runtime directories /workspace or /tmp/a13n are not writable by {user}"
            else:
                state_error = container.attrs.get("State", {}).get("Error", "")
                if configuration.python in state_error:
                    return f"Configured Python {configuration.python} cannot start in the image: {state_error}"
    except Exception:
        pass
    return f"Docker preparation failed: {error}"
