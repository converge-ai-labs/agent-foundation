"""Run installed and explicit Environment run-extension factories end to end."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from a13n_environment_provider import (
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentProviderSpec,
    build_environment_provider_factory_catalog,
)
from a13n_harness import (
    AgentSpec,
    EnvironmentAction,
    EnvironmentPermissionSet,
    EnvironmentRunExtensionFactoryCatalog,
    EnvironmentRunExtensionFactoryContext,
    HarnessBuilder,
    RunBindings,
    RunPreparationContext,
    build_environment_run_extension_factory_catalog,
    discover_environment_run_extension_factory_references,
)
from a13n_harness.environment.advanced import (
    EnvironmentRuntimeMount,
    create_environment_provider_binding,
    create_environment_runtime,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

EXTENSION_KEY = "example.workspace-marker"
type ExtensionSelectionMode = Literal["entrypoint", "code"]


def _offline_model() -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages, info
        yield "extension observed"

    return FunctionModel(stream_function=stream)


@dataclass(frozen=True, slots=True)
class EnvironmentExtensionDemoResult:
    selection_mode: ExtensionSelectionMode
    extension_key: str
    extension_id: str
    run_id: str
    marker_text: str
    marker_removed: bool


async def _run_extension_demo(
    *,
    selection_mode: ExtensionSelectionMode,
    catalog: EnvironmentRunExtensionFactoryCatalog,
    workspace_root: Path,
) -> EnvironmentExtensionDemoResult:
    from a13n_plugin_examples.environment import (
        WorkspaceEnvironmentProviderFactory,
        WorkspaceEnvironmentRuntime,
    )

    provider_catalog = build_environment_provider_factory_catalog(
        explicit_factories=(WorkspaceEnvironmentProviderFactory(),)
    )
    manager = provider_catalog.create_provider(
        EnvironmentProviderSpec(
            provider_key="example.workspace",
            schema_version="1",
            parameters={
                "root": str(workspace_root),
                "environment_id": "extension-workspace",
                "read_only": False,
            },
        ),
        runtime=WorkspaceEnvironmentRuntime(),
    )
    operation = EnvironmentOperationContext(
        operation_id=f"operation-create-{selection_mode}",
        action=EnvironmentManagementAction.CREATE,
        resource_correlation=f"resource-extension-{selection_mode}",
        attempt=1,
    )
    extension_id = f"marker-{selection_mode}"
    marker_path = "/workspace/.example-run"
    extension = catalog.create_extension(
        EnvironmentRunExtensionFactoryContext(
            extension_key=EXTENSION_KEY,
            extension_id=extension_id,
            configuration={"marker_path": marker_path, "label": selection_mode},
        )
    )
    marker_text: str | None = None
    marker_removed = False

    async def read_marker(context: RunPreparationContext) -> str:
        nonlocal marker_text
        marker_text = (await context.environment.files.read_text(marker_path)).text
        return "Confirm that the Environment run extension is active."

    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(),
        output_type=str,
        model=_offline_model(),
    )
    resource = await manager.create(operation=operation)
    try:
        async with executable, resource:
            async with resource.acquire_attachment() as attachment:
                provider = create_environment_provider_binding(attachment)
                environment_runtime = create_environment_runtime(
                    mounts={
                        "workspace": EnvironmentRuntimeMount(
                            binding=provider,
                            permission_ceiling=EnvironmentPermissionSet(
                                operations=frozenset(
                                    {
                                        EnvironmentAction.FILE_READ_TEXT,
                                        EnvironmentAction.FILE_WRITE_TEXT,
                                        EnvironmentAction.FILE_REMOVE,
                                    }
                                )
                            ),
                            working_directory="/",
                        )
                    },
                    default_mount="workspace",
                    extensions=(extension,),
                )
                result = await executable.run(
                    input_factory=read_marker,
                    bindings=RunBindings.embedded(environment=environment_runtime),
                )
                if result.output_or_raise() != "extension observed":
                    raise RuntimeError("The offline Agent returned an unexpected result")
                marker_removed = not (workspace_root / ".example-run").exists()
    finally:
        await manager.destroy(
            resource.state,
            operation=EnvironmentOperationContext(
                operation_id=f"operation-destroy-{selection_mode}",
                action=EnvironmentManagementAction.DESTROY,
                resource_correlation=f"resource-extension-{selection_mode}",
                attempt=1,
            ),
        )

    return EnvironmentExtensionDemoResult(
        selection_mode=selection_mode,
        extension_key=catalog.registrations[0].extension_key,
        extension_id=extension.extension_id,
        run_id=result.run_id,
        marker_text=marker_text or "",
        marker_removed=marker_removed,
    )


async def run_environment_extension_entrypoint_demo(
    *,
    workspace_root: Path,
) -> EnvironmentExtensionDemoResult:
    """Discover and select the installed Environment run-extension factory."""

    references = discover_environment_run_extension_factory_references()
    if EXTENSION_KEY not in {reference.extension_key for reference in references}:
        raise RuntimeError(f"Installed Environment run-extension factory {EXTENSION_KEY!r} was not discovered")
    catalog = build_environment_run_extension_factory_catalog(extension_keys=(EXTENSION_KEY,))
    return await _run_extension_demo(
        selection_mode="entrypoint",
        catalog=catalog,
        workspace_root=workspace_root,
    )


async def run_environment_extension_code_demo(
    *,
    workspace_root: Path,
) -> EnvironmentExtensionDemoResult:
    """Supply an explicit factory object without scanning package metadata."""

    from a13n_plugin_examples.environment_extension import WorkspaceMarkerExtensionFactory

    catalog = build_environment_run_extension_factory_catalog(explicit_factories=(WorkspaceMarkerExtensionFactory(),))
    return await _run_extension_demo(
        selection_mode="code",
        catalog=catalog,
        workspace_root=workspace_root,
    )


def _run_main(selection_mode: ExtensionSelectionMode) -> None:
    with TemporaryDirectory(prefix="a13n-extension-") as workspace_value:
        workspace_root = Path(workspace_value)
        if selection_mode == "entrypoint":
            coroutine = run_environment_extension_entrypoint_demo(workspace_root=workspace_root)
        else:
            coroutine = run_environment_extension_code_demo(workspace_root=workspace_root)
        result = asyncio.run(coroutine)

    print(f"selection mode: {result.selection_mode}")
    print(f"selected extension: {result.extension_key}")
    print(f"extension id: {result.extension_id}")
    print(f"marker content: {result.marker_text.strip()}")
    print(f"marker removed: {result.marker_removed}")


def main_entrypoint() -> None:
    """Run the package entry-point path without model credentials."""

    _run_main("entrypoint")


def main_code() -> None:
    """Run the explicit factory-object path without model credentials."""

    _run_main("code")


if __name__ == "__main__":
    main_entrypoint()
