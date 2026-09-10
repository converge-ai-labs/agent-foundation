"""A real file effect with optional tool-owned idempotency and crash barriers."""

import os
import re
from pathlib import Path
from uuid import uuid4

import anyio
from a13n_harness import AbstractHarnessPlugin, AgentContext
from a13n_harness.plugin_factories import HarnessPluginFactory
from pydantic import BaseModel
from pydantic_ai import ModelRetry, Tool
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from .resilience_plugin import Configuration
from .run_faults import Faults


class Capability(AbstractCapability[AgentContext]):
    id = "live-run-faults"

    def __init__(self, root):
        self.root = Path(root)
        self.faults = Faults(self.root.parent / "faults", "worker")

    def get_toolset(self):
        async def live_fault_effect(case_id: str, token: str, idempotent: bool = False, failure: str = "") -> str:
            """Create a test-owned file, then return its contents."""
            if not all(re.fullmatch(r"[a-f0-9]{32}", value) for value in (case_id, token)):
                raise ValueError("Invalid fault-test identity")
            root = self.root / case_id
            if not await anyio.Path(root / "case.json").is_file():
                raise ValueError("Unknown test case")
            async with await anyio.Path(root / "effect_attempts").open("a") as log:
                await log.write("attempt\n")
            facts = {"case_id": case_id}
            await self.faults.reach("tool.before_effect", **facts)
            if failure == "exception":
                raise ValueError("Injected tool exception")
            if failure == "timeout":
                with anyio.fail_after(0.1):
                    await anyio.sleep(10)
            if failure == "retry":
                raise ModelRetry("Injected retryable tool failure")

            def effect():
                # The file itself is the business effect. Its stable name is the
                # tool-owned idempotency key; there is no separate receipt gap.
                path = root / ("business-effect-" + (token if idempotent else uuid4().hex))
                try:
                    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    assert idempotent and path.read_text() == token
                else:
                    with os.fdopen(descriptor, "w") as output:
                        output.write(token)
                return path

            path = await anyio.to_thread.run_sync(effect)
            await self.faults.reach("tool.after_effect", **facts)
            return await anyio.Path(path).read_text()

        return FunctionToolset([Tool(live_fault_effect)], id=self.id)


class Plugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id, root, state_version=None):
        self._id, self.root, self.state_version = plugin_id, root, state_version

    @property
    def plugin_id(self):
        return self._id

    def get_capabilities(self):
        return [Capability(self.root)]

    async def for_run(self, context):
        if self.state_version is not None:
            state = await context.state.read(self.plugin_id, PluginState, version=self.state_version)
            await context.state.write(self.plugin_id, state or PluginState(), version=self.state_version)
        return self


class PluginState(BaseModel):
    initialized: bool = True


class Factory(HarnessPluginFactory):
    def __init__(self, state_version=None):
        self.state_version = state_version

    @classmethod
    def plugin_key(cls):
        return "live.run_faults"

    def validate_configuration(self, configuration):
        return Configuration.model_validate(dict(configuration))

    def create_plugin(self, context):
        return Plugin(context.plugin_id, context.configuration["root"], self.state_version)
