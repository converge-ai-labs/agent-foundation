"""A real uploaded tool fixture for checkpoint effects and bounded tool failures."""

from __future__ import annotations

import re
from pathlib import Path

import anyio
from a13n_harness import AbstractHarnessPlugin, AgentContext
from a13n_harness.plugin_factories import HarnessPluginFactory
from pydantic import BaseModel, ConfigDict
from pydantic_ai import ModelRetry, Tool
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str


class ResilienceCapability(AbstractCapability[AgentContext]):
    id = "live-resilience"

    def __init__(self, root: str):
        self.root = Path(root)

    def get_toolset(self):
        async def live_effect(case_id: str, token: str, fail: bool = False) -> str:
            """Record a real local effect, or report a controlled retryable tool error."""
            if not all(re.fullmatch(r"[a-f0-9]{32}", value) for value in (case_id, token)):
                raise ValueError("Invalid live-test identity")
            path = anyio.Path(self.root / case_id)
            if not await (path / "case.json").is_file():
                raise ValueError("Unknown live-test case")
            async with await (path / "effect_attempts").open("a") as output:
                await output.write("attempt\n")
            if fail:
                raise ModelRetry("live_test_tool_failure")
            # Deliberately non-idempotent: duplicate execution remains visible after recovery.
            async with await (path / "effects").open("a") as output:
                await output.write(token + "\n")
            return token

        return FunctionToolset([Tool(live_effect)], id="live-resilience-tools")


class Plugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str, root: str):
        self._id, self.root = plugin_id, root

    @property
    def plugin_id(self):
        return self._id

    def get_capabilities(self):
        return [ResilienceCapability(self.root)]


class Factory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls):
        return "live.resilience"

    def validate_configuration(self, configuration):
        return Configuration.model_validate(dict(configuration))

    def create_plugin(self, context):
        return Plugin(context.plugin_id, Configuration.model_validate(dict(context.configuration)).root)
