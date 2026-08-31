"""Run packaged and explicit Environment provider factories through a real runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from a13n_environment_provider import (
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentProviderFactoryCatalog,
    EnvironmentProviderSpec,
    build_environment_provider_factory_catalog,
    discover_environment_provider_factory_references,
)
from a13n_harness import RunBindings
from a13n_harness.environment import (
    EnvironmentAction,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import (
    EnvironmentProviderBinding,
    EnvironmentRuntimeMount,
    create_environment_provider_binding,
    create_environment_runtime,
)
from pydantic import JsonValue

# Host configuration selects this metadata key without importing its target.
PROVIDER_KEY = "example.workspace"
type EnvironmentSelectionMode = Literal["entrypoint", "code"]


@dataclass(frozen=True, slots=True)
class EnvironmentDemoResult:
    selection_mode: EnvironmentSelectionMode
    provider_key: str
    aliases: tuple[str, ...]
    default_text: str
    docs_text: str
    durable_lifecycle_phases: tuple[str, ...]
    pause_supported: bool


def _provider_spec(root: Path, environment_id: str) -> EnvironmentProviderSpec:
    parameters: dict[str, JsonValue] = {
        "root": str(root),
        "environment_id": environment_id,
        "read_only": True,
    }
    return EnvironmentProviderSpec(
        provider_key=PROVIDER_KEY,
        schema_version="1",
        parameters=parameters,
    )


def _operation(action: EnvironmentManagementAction, environment_id: str) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=f"operation-{action.value}-{environment_id}",
        action=action,
        resource_correlation=f"resource-{environment_id}",
        attempt=1,
    )


def _runtime_mount(provider_binding: EnvironmentProviderBinding) -> EnvironmentRuntimeMount:
    return EnvironmentRuntimeMount(
        binding=provider_binding,
        permission_ceiling=EnvironmentPermissionSet(operations=frozenset({EnvironmentAction.FILE_READ_TEXT})),
        working_directory="/",
    )


async def _run_environment_demo(
    *,
    selection_mode: EnvironmentSelectionMode,
    catalog: EnvironmentProviderFactoryCatalog,
    source_root: Path,
    docs_root: Path,
) -> EnvironmentDemoResult:
    from a13n_plugin_examples.environment import WorkspaceEnvironmentRuntime

    source_provider = catalog.create_provider(
        _provider_spec(source_root, "workspace-source"),
        runtime=WorkspaceEnvironmentRuntime(),
    )
    docs_provider = catalog.create_provider(
        _provider_spec(docs_root, "workspace-docs"),
        runtime=WorkspaceEnvironmentRuntime(),
    )
    source_create = _operation(EnvironmentManagementAction.CREATE, "workspace-source")
    docs_create = _operation(EnvironmentManagementAction.CREATE, "workspace-docs")
    source_resource = await source_provider.create(operation=source_create)
    docs_resource = await docs_provider.create(operation=docs_create)
    source_state = source_resource.state
    docs_state = docs_resource.state

    async with source_resource, docs_resource:
        async with (
            source_resource.acquire_attachment() as source_attachment,
            docs_resource.acquire_attachment() as docs_attachment,
        ):
            source = create_environment_provider_binding(source_attachment)
            docs = create_environment_provider_binding(docs_attachment)
            environment_runtime = create_environment_runtime(
                mounts={
                    "source": _runtime_mount(source),
                    "docs": _runtime_mount(docs),
                },
                default_mount="source",
            )
            run_bindings = RunBindings.embedded(environment=environment_runtime)

            # HarnessRunStream performs this same runtime bind/activate lifecycle.
            async with environment_runtime.bind(
                run_id="run-example",
                instance=run_bindings.instance,
            ) as environment:
                await environment_runtime._activate()
                default_page = await environment.files.read_text("/workspace/message.txt")
                docs_page = await environment.files.read_text("/environment/docs/message.txt")
                aliases = tuple(mount.name for mount in environment.snapshot.mounts)

    created = await source_provider.reconcile(source_create, last_known_state=source_state)
    source_resume = _operation(EnvironmentManagementAction.RESUME, "workspace-source")
    resumed_resource = await source_provider.resume(source_state, operation=source_resume)
    async with resumed_resource:
        async with resumed_resource.acquire_attachment():
            resumed_state = resumed_resource.state
    resumed = await source_provider.reconcile(source_resume, last_known_state=resumed_state)

    source_destroy = _operation(EnvironmentManagementAction.DESTROY, "workspace-source")
    await source_provider.destroy(resumed_state, operation=source_destroy)
    destroyed = await source_provider.reconcile(source_destroy, last_known_state=resumed_state)
    await docs_provider.destroy(
        docs_state,
        operation=_operation(EnvironmentManagementAction.DESTROY, "workspace-docs"),
    )

    return EnvironmentDemoResult(
        selection_mode=selection_mode,
        provider_key=catalog.registrations[0].provider_key,
        aliases=aliases,
        default_text=default_page.text,
        docs_text=docs_page.text,
        durable_lifecycle_phases=(created.phase.value, resumed.phase.value, destroyed.phase.value),
        pause_supported=bool(source_provider.lifecycle_capabilities.pause_modes),
    )


async def run_environment_entrypoint_demo(
    *,
    source_root: Path,
    docs_root: Path,
) -> EnvironmentDemoResult:
    """Discover and explicitly select the installed package factory."""

    references = discover_environment_provider_factory_references()
    if PROVIDER_KEY not in {reference.provider_key for reference in references}:
        raise RuntimeError(f"Installed Environment provider factory {PROVIDER_KEY!r} was not discovered")
    catalog = build_environment_provider_factory_catalog(extension_keys=(PROVIDER_KEY,))
    return await _run_environment_demo(
        selection_mode="entrypoint",
        catalog=catalog,
        source_root=source_root,
        docs_root=docs_root,
    )


async def run_environment_code_demo(
    *,
    source_root: Path,
    docs_root: Path,
) -> EnvironmentDemoResult:
    """Supply the factory object directly while retaining its factory method."""

    from a13n_plugin_examples.environment import WorkspaceEnvironmentProviderFactory

    catalog = build_environment_provider_factory_catalog(explicit_factories=(WorkspaceEnvironmentProviderFactory(),))
    return await _run_environment_demo(
        selection_mode="code",
        catalog=catalog,
        source_root=source_root,
        docs_root=docs_root,
    )


def _run_main(selection_mode: EnvironmentSelectionMode) -> None:
    with TemporaryDirectory(prefix="a13n-source-") as source_value:
        with TemporaryDirectory(prefix="a13n-docs-") as docs_value:
            source_root = Path(source_value)
            docs_root = Path(docs_value)
            (source_root / "message.txt").write_text("source workspace\n", encoding="utf-8")
            (docs_root / "message.txt").write_text("documentation workspace\n", encoding="utf-8")
            if selection_mode == "entrypoint":
                coroutine = run_environment_entrypoint_demo(source_root=source_root, docs_root=docs_root)
            else:
                coroutine = run_environment_code_demo(source_root=source_root, docs_root=docs_root)
            result = asyncio.run(coroutine)

    print(f"selection mode: {result.selection_mode}")
    print(f"selected provider: {result.provider_key}")
    print(f"active aliases: {', '.join(result.aliases)}")
    print(f"default route: {result.default_text.strip()}")
    print(f"docs route: {result.docs_text.strip()}")
    print(f"durable lifecycle: {' -> '.join(result.durable_lifecycle_phases)}")
    print(f"pause supported: {result.pause_supported}")


def main_entrypoint() -> None:
    """Run the package entry-point path without model credentials."""

    _run_main("entrypoint")


def main_code() -> None:
    """Run the explicit factory-object path without model credentials."""

    _run_main("code")


if __name__ == "__main__":
    main_entrypoint()
