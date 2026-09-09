"""Trusted approval plugin loaded by the live-test Worker at startup."""

from __future__ import annotations

import re
from pathlib import Path

import anyio
from a13n_harness import AbstractHarnessPlugin, AgentContext
from a13n_harness.plugin_factories import HarnessPluginFactory
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Tool
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str


class ApprovalCapability(AbstractCapability[AgentContext]):
    id = "live-approval"

    def __init__(self, root: str):
        self.root = Path(root)

    def get_toolset(self):
        async def live_approved_write(case_id: str, token: str) -> str:
            """Write a test token only after native approval has been granted."""
            if not re.fullmatch(r"[a-f0-9]{32}", case_id) or not re.fullmatch(r"[a-f0-9]{32}", token):
                raise ValueError("Invalid live-test identity")
            path = self.root / case_id
            if not await anyio.Path(path / "case.json").is_file():
                raise ValueError("Unknown live-test case")
            await anyio.Path(path / "output").write_text(token)
            async with await anyio.open_file(path / "approval_executions", "a") as output:
                await output.write("executed\n")
            return token

        return FunctionToolset([Tool(live_approved_write, requires_approval=True)], id="live-approval-tools")


class Plugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str, root: str):
        self._id, self.root = plugin_id, root

    @property
    def plugin_id(self):
        return self._id

    def get_capabilities(self):
        return [ApprovalCapability(self.root)]


class Factory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls):
        return "live.approval"

    def validate_configuration(self, configuration):
        return Configuration.model_validate(dict(configuration))

    def create_plugin(self, context):
        parsed = Configuration.model_validate(dict(context.configuration))
        return Plugin(context.plugin_id, parsed.root)
