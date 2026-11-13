"""Bot discovery and activation of captured Project Environments."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from a13n_harness.context import AgentContext
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import EnvironmentRuntime
from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import AbstractModelContextCapability
from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness_ui.composition import ResolvedRunComposition
from a13n_harness_ui.environment_runtime import EnvironmentRunService, target_mount_aliases
from a13n_harness_ui.errors import HarnessUiError


@dataclass(kw_only=True)
class BotEnvironmentsCapability(AbstractModelContextCapability):
    service: EnvironmentRunService
    composition: ResolvedRunComposition
    runtime: EnvironmentRuntime
    id: str | None = "a13n.harness-ui.bot-environments"
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def get_instructions(self) -> str:
        return (
            "Bot can directly use all captured Project Environments. Use list_environment_targets to discover "
            "targets and open_environment_target to mount one in this Run. Then use ordinary Files paths and "
            "Shell/Computer aliases returned by that tool. The default remains Bot's own workspace. "
            "host-desktop opens Host Computer Use when enabled and available. Browser is Host-owned and "
            "independent of Project mounts. Never create a Thread merely to access its Project."
        )

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                HarnessTool(
                    self.list_environment_targets,
                    harness_metadata=HarnessToolMetadata(
                        tool_id="a13n-harness-ui.bot.list_environment_targets",
                        effects=frozenset({"read"}),
                        credential_audiences=(),
                        idempotency="read_only",
                        output_policy=ToolOutputPolicy(
                            max_inline_bytes=65536, max_output_bytes=65536, overflow="truncate"
                        ),
                    ),
                ),
                HarnessTool(
                    self.open_environment_target,
                    harness_metadata=HarnessToolMetadata(
                        tool_id="a13n-harness-ui.bot.open_environment_target",
                        effects=frozenset({"read", "write"}),
                        credential_audiences=(),
                        idempotency="none",
                        output_policy=ToolOutputPolicy(
                            max_inline_bytes=65536, max_output_bytes=65536, overflow="truncate"
                        ),
                    ),
                ),
            ],
            id="a13n-harness-ui-bot-environments",
        )

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.deps.thread_id != self.composition.thread_id or self.composition.root.source_kind != "bot":
            raise DefinitionError(
                "Bot Environment tools belong to their captured root Run.", code="capability_scope_invalid"
            )

    async def list_environment_targets(
        self,
        ctx: RunContext[AgentContext],
        query: str = "",
        offset: int = Field(default=0, ge=0),
        limit: int = Field(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        """Discover captured Projects and the Host desktop without opening their connections."""
        self._require_context(ctx)
        targets = [
            target
            for target in self.composition.environment_targets
            if query.casefold() in f"{target.target_id} {target.name}".casefold()
        ]
        selected = targets[offset : offset + limit]
        return {
            "generation_digest": self.composition.generation_digest,
            "targets": [
                {
                    "target_id": target.target_id,
                    "name": target.name,
                    "project_id": target.project_id,
                    "environment_profile_id": target.environment_profile.profile_id,
                    "mount_count": len(target_mount_aliases(target)),
                    "unavailable": target.unavailable,
                }
                for target in selected
            ],
            "next_offset": offset + len(selected) if offset + len(selected) < len(targets) else None,
        }

    async def open_environment_target(
        self,
        ctx: RunContext[AgentContext],
        target_id: str,
    ) -> dict[str, Any]:
        """Mount a captured target for native Files, Shell and Computer tools; keep Bot's default workspace."""
        self._require_context(ctx)
        target = next((item for item in self.composition.environment_targets if item.target_id == target_id), None)
        if target is None:
            return {
                "ok": False,
                "error": {"code": "bot_environment_missing", "message": "Target is absent from this Run's capture."},
            }
        environment = ctx.deps.environment
        assert environment is not None
        async with self._lock:
            try:
                await self.service.mount_target(
                    self.composition,
                    target,
                    self.runtime,
                    existing_aliases={mount.name for mount in environment.snapshot.mounts},
                )
            except (HarnessUiError, EnvironmentError, OSError) as error:
                return {
                    "ok": False,
                    "error": {
                        "code": error.code
                        if isinstance(error, (HarnessUiError, EnvironmentError))
                        else "bot_environment_unavailable",
                        "message": str(error),
                    },
                }
        aliases = target_mount_aliases(target)
        return {
            "ok": True,
            "target_id": target_id,
            "mounts": [
                {"source_alias": local, "alias": alias, "path": f"/environment/{alias}"}
                for local, alias in aliases.items()
            ],
            "preferred_alias": aliases.get(target.default_environment or next(iter(aliases), "")),
            "default_unchanged": True,
        }
