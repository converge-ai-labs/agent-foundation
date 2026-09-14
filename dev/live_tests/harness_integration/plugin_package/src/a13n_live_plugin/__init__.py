"""A standalone installed plugin; no dependency on the live-test host or checkout."""

import hashlib
import json
import os
from importlib.metadata import version

import anyio
from a13n_harness import AbstractHarnessPlugin, AgentContext, HarnessRunResult
from a13n_harness.plugin_factories import HarnessPluginFactory
from a13n_harness.plugins import PluginRunResponse
from pydantic import BaseModel, ConfigDict
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    root: str
    label: str


async def record(root, event):
    async with await (anyio.Path(root) / "events.jsonl").open("a") as output:
        await output.write(json.dumps(event) + "\n")


class Capability(AbstractCapability[AgentContext]):
    id = "packaged-effect"

    def __init__(self, plugin_id, configuration):
        self.plugin_id, self.configuration = plugin_id, configuration

    def get_toolset(self):
        async def packaged_effect(value: str) -> dict:
            """Hash the supplied value with the configured label and record one real effect."""
            result = {
                "digest": hashlib.sha256(f"{self.configuration.label}:{value}".encode()).hexdigest(),
                "label": self.configuration.label,
                "plugin_id": self.plugin_id,
                "distribution_version": version("a13n-live-plugin"),
                "module_file": __file__,
                "uid": os.getuid(),
            }
            await record(self.configuration.root, {"event": "tool", **result})
            return result

        return FunctionToolset([packaged_effect], id="packaged-tools")


class Plugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id, configuration):
        self._id, self.configuration = plugin_id, configuration

    @property
    def plugin_id(self):
        return self._id

    def get_capabilities(self):
        return [Capability(self.plugin_id, self.configuration)]

    def wrap_run(self, exchange, call_next):
        async def iterate():
            async for item in call_next(exchange):
                if isinstance(item, HarnessRunResult):
                    await record(
                        self.configuration.root,
                        {
                            "event": "result",
                            "run_id": exchange.context.run_id,
                            "plugin_id": self.plugin_id,
                            "status": item.status,
                        },
                    )
                yield item

        return PluginRunResponse(iterate())


class Factory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls):
        return "live.packaged"

    def validate_configuration(self, configuration):
        return Configuration.model_validate(dict(configuration))

    def create_plugin(self, context):
        return Plugin(context.plugin_id, Configuration.model_validate(dict(context.configuration)))
