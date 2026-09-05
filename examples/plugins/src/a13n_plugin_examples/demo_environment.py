"""Run packaged and explicit Environment Providers through a real Harness Run."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from a13n_environment_provider import (
    EnvironmentProvider,
    EnvironmentProviderCatalog,
    build_environment_provider_catalog,
)
from a13n_harness import (
    AgentSpec,
    EnvironmentAccess,
    EnvironmentMount,
    HarnessBuilder,
    RunPreparationContext,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

PROVIDER_KEY = "example.workspace"
type EnvironmentSelectionMode = Literal["entrypoint", "code"]


@dataclass(frozen=True, slots=True)
class EnvironmentDemoResult:
    selection_mode: EnvironmentSelectionMode
    provider_key: str
    aliases: tuple[str, ...]
    default_text: str
    docs_text: str
    exported_state_aliases: tuple[str, ...]
    roots_preserved: bool


def _configuration(provider: EnvironmentProvider, root: Path):
    return provider.validate_configuration(
        schema_version="1",
        value={
            "root": str(root),
            "read_only": True,
        },
    )


async def _run_environment_demo(
    *,
    selection_mode: EnvironmentSelectionMode,
    catalog: EnvironmentProviderCatalog,
    source_root: Path,
    docs_root: Path,
) -> EnvironmentDemoResult:
    provider = catalog.require(PROVIDER_KEY)
    source = provider.create_environment(
        environment_id="workspace-source",
        configuration=_configuration(provider, source_root),
        state=None,
        runtime=None,
    )
    docs = provider.create_environment(
        environment_id="workspace-docs",
        configuration=_configuration(provider, docs_root),
        state=None,
        runtime=None,
    )
    default_text = ""
    docs_text = ""
    aliases: tuple[str, ...] = ()

    async def read_workspaces(context: RunPreparationContext) -> str:
        nonlocal aliases, default_text, docs_text
        default_text = (await context.environment.files.read_text("/workspace/message.txt")).text
        docs_text = (await context.environment.files.read_text("/environment/docs/message.txt")).text
        aliases = tuple(mount.name for mount in context.environment.snapshot.mounts)
        return "Confirm that both workspaces were read."

    async def stream_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages, info
        yield "workspaces observed"

    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream_model),
    )
    result = await executable.run(
        input_factory=read_workspaces,
        environments={
            "source": EnvironmentMount(source, access=EnvironmentAccess.READ_ONLY),
            "docs": EnvironmentMount(docs, access=EnvironmentAccess.READ_ONLY),
        },
        default_environment="source",
    )
    if result.output_or_raise() != "workspaces observed":
        raise RuntimeError("The offline Agent returned an unexpected result")
    if result.state is None:
        raise RuntimeError("The Harness Run did not export continuation state")

    return EnvironmentDemoResult(
        selection_mode=selection_mode,
        provider_key=provider.key,
        aliases=aliases,
        default_text=default_text,
        docs_text=docs_text,
        exported_state_aliases=tuple(sorted(result.state.environment_states)),
        roots_preserved=source_root.is_dir() and docs_root.is_dir(),
    )


async def run_environment_entrypoint_demo(
    *,
    source_root: Path,
    docs_root: Path,
) -> EnvironmentDemoResult:
    """Load only the explicitly enabled installed Provider entry point."""

    catalog = build_environment_provider_catalog(extension_keys=(PROVIDER_KEY,))
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
    """Register the Provider object directly without scanning package metadata."""

    from a13n_plugin_examples.environment import WorkspaceEnvironmentProvider

    catalog = build_environment_provider_catalog(
        explicit_providers=(WorkspaceEnvironmentProvider(),),
    )
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
    print(f"exported state aliases: {', '.join(result.exported_state_aliases) or 'none'}")
    print(f"roots preserved: {result.roots_preserved}")


def main_entrypoint() -> None:
    """Run the package entry-point path without model credentials."""

    _run_main("entrypoint")


def main_code() -> None:
    """Run the explicit Provider-object path without model credentials."""

    _run_main("code")


if __name__ == "__main__":
    main_entrypoint()
