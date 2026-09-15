"""Compose middleware and one definition-owned Web Capability with Host bindings."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import (
    WebBinding,
    WebCapability,
    WebConfiguration,
    WebPolicy,
    WebRequest,
    WebResponse,
    WebScrapeConfiguration,
    WebSearchConfiguration,
)
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

_EXAMPLE_URL = "https://example.com/offline"
_EXAMPLE_CONTENT = "Host-owned offline Web response"


class _OfflineWeb:
    """An exact fixture policy/client; this example never opens a network socket."""

    async def authorize(self, url: str, *, purpose: str) -> None:
        if url != _EXAMPLE_URL or purpose != "fetch":
            raise RuntimeError("The offline example only supports its one fixture URL")

    async def request(self, request: WebRequest, *, policy: WebPolicy) -> WebResponse:
        await policy.authorize(request.url, purpose=request.purpose)

        async def body() -> AsyncIterator[bytes]:
            yield _EXAMPLE_CONTENT.encode()

        return WebResponse(
            status_code=200,
            final_url=request.url,
            canonical_url=request.url,
            headers={"content-type": "text/plain"},
            body=body(),
        )


async def run_web_demo() -> str:
    """Observe a Web-enabled Run with middleware and return the fixture content."""

    from a13n_plugin_examples.harness import RunRecorderPlugin

    recorder = RunRecorderPlugin(plugin_id="recorder-web")

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        if {tool.name for tool in info.function_tools} != {"fetch", "download"}:
            raise RuntimeError("The definition must select the configured Web feature")
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="fetch",
                    json_args='{"url":"https://example.com/offline"}',
                    tool_call_id="offline-fetch",
                )
            }
        else:
            if _EXAMPLE_CONTENT not in str(returns[-1].content):
                raise RuntimeError("The feature did not use the Host's Web binding")
            yield _EXAMPLE_CONTENT

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        plugins=(recorder,),
        capabilities=(
            WebCapability(
                WebConfiguration(
                    search=WebSearchConfiguration(mode="off"),
                    scrape=WebScrapeConfiguration(mode="off"),
                )
            ),
        ),
    )
    # Each invocation supplies fresh binding values. A real Host also owns its
    # transport scope and credential resolution; neither belongs in plugin YAML.
    provider = _OfflineWeb()
    result = await executable.run(
        "Fetch the offline fixture.",
        bindings=RunBindings.embedded(web=WebBinding(client=provider, policy=provider)),
    )
    output = result.output_or_raise()
    if len(recorder.observations) != 1 or recorder.observations[0].status != "completed":
        raise RuntimeError("The middleware must observe one completed Web-enabled Run")
    return output


if __name__ == "__main__":
    print(asyncio.run(run_web_demo()))
