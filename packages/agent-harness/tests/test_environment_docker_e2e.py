from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from a13n_environment_provider import (
    DirectoryDockerBootstrapStore,
    DockerEnvironmentProvider,
    DockerImagePullPolicy,
    DockerProviderConfiguration,
    DockerProviderRuntime,
    DockerSDKEngine,
    EIPEnvironmentAttachment,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
)
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    CommandRequest,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentOutputPolicy,
    EnvironmentPermissionSet,
    PortTarget,
    ShellCommand,
)
from a13n_harness.environment.advanced import (
    EnvironmentBindingRequest,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    create_environment_provider_binding,
    create_environment_run_binding,
)

_ENVIRONMENT_ID = "docker-harness-e2e"
_RESOURCE_CORRELATION = "resource-docker-harness-e2e"


def _docker_image() -> str:
    image = os.environ.get("A13N_DOCKER_E2E_IMAGE")
    if image is None:
        pytest.skip("set A13N_DOCKER_E2E_IMAGE to run the Docker Provider E2E test")
    return image


def _operation(action: EnvironmentManagementAction, suffix: str) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=f"operation-docker-{suffix}",
        action=action,
        resource_correlation=_RESOURCE_CORRELATION,
        attempt=1,
    )


def _run_binding(attachment: EIPEnvironmentAttachment, *, revision: int):
    return create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=revision,
            bindings=(
                EnvironmentBindingRequest(
                    binding_id="binding-docker",
                    binding_revision=revision,
                    alias="workspace",
                    permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                    default_working_directory="/",
                    provider_binding=create_environment_provider_binding(attachment),
                ),
            ),
            default_binding_id="binding-docker",
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def test_docker_provider_runs_harness_eip_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider = DockerEnvironmentProvider(
            DockerProviderConfiguration(
                environment_id=_ENVIRONMENT_ID,
                image=_docker_image(),
                pull_policy=DockerImagePullPolicy.NEVER,
                max_output_preview_bytes=1024,
                max_output_bytes_per_stream=1024,
                max_spool_bytes=2048,
            ),
            DockerProviderRuntime(
                engine=DockerSDKEngine.from_env(),
                bootstrap_store=DirectoryDockerBootstrapStore((tmp_path / "bootstrap").resolve()),
            ),
        )
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="agent"),
            agent_instance_id="agent-docker-e2e",
        )
        output_policy = EnvironmentOutputPolicy(
            max_inline_bytes=4,
            max_output_bytes=64,
            overflow="retain",
        )
        resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))
        state = resource.state
        old_handle = None
        first_generation = None
        try:
            async with resource:
                async with resource.acquire_attachment() as attachment:
                    binding = _run_binding(attachment, revision=1)
                    async with binding.bind(run_id="run-docker-1", instance=instance) as environment:
                        await environment.activate()
                        await environment.files.write_text(
                            "/workspace/message.txt",
                            "docker harness",
                            mode="create",
                        )
                        message = await environment.files.read_text("/workspace/message.txt")
                        assert message.text == "docker harness"
                        listening = await environment.ports.wait(
                            PortTarget(port=8787),
                            desired="listening",
                            timeout_seconds=5,
                        )
                        assert listening.status == "listening"

                        shell_result = await environment.shell.exec(
                            CommandRequest(
                                command=ShellCommand(
                                    profile_id="bash",
                                    script="printf retained-output-value",
                                ),
                                output_policy=output_policy,
                            )
                        )
                        first_generation = shell_result.receipt.observed_generation
                        reference = shell_result.output.stdout.reference
                        assert reference is not None
                        retained = await environment.outputs.read(reference, policy=output_policy)
                        chunks = list(retained.chunks)
                        while retained.next_cursor is not None:
                            retained = await environment.outputs.read(
                                reference,
                                cursor=retained.next_cursor,
                                policy=output_policy,
                            )
                            chunks.extend(retained.chunks)
                        assert b"".join(chunk.data for chunk in chunks) == b"retained-output-value"
                        await environment.outputs.release(reference=reference)

                        started = await environment.processes.start(
                            CommandRequest(
                                command=ShellCommand(profile_id="bash", script="sleep 30"),
                                output_policy=output_policy,
                            )
                        )
                        old_handle = started.process.handle
                        await environment.processes.kill(old_handle)
                        completed = await environment.processes.wait(
                            old_handle,
                            condition="tree_cleaned",
                            timeout_seconds=5,
                        )
                        assert completed.status.cleanup == "complete"
                        await environment.processes.release(old_handle)

                paused = await provider.pause(
                    resource,
                    operation=_operation(EnvironmentManagementAction.PAUSE, "pause"),
                    mode=EnvironmentPauseMode.FILESYSTEM,
                )
                state = paused

            resumed = await provider.resume(
                state,
                operation=_operation(EnvironmentManagementAction.RESUME, "resume"),
            )
            state = resumed.state
            async with resumed:
                async with resumed.acquire_attachment() as attachment:
                    binding = _run_binding(attachment, revision=2)
                    async with binding.bind(run_id="run-docker-2", instance=instance) as environment:
                        await environment.activate()
                        message = await environment.files.read_text("/workspace/message.txt")
                        assert message.text == "docker harness"
                        result = await environment.shell.exec(
                            CommandRequest(
                                command=ShellCommand(profile_id="bash", script="printf resumed"),
                                output_policy=output_policy,
                            )
                        )
                        assert result.receipt.observed_generation != first_generation
                        assert old_handle is not None
                        with pytest.raises(EnvironmentError) as stale:
                            await environment.processes.inspect(old_handle)
                        assert stale.value.code == "environment_stale_binding"
                state = resumed.state
        finally:
            await provider.destroy(
                state,
                operation=_operation(EnvironmentManagementAction.DESTROY, "destroy"),
            )

    asyncio.run(scenario())
