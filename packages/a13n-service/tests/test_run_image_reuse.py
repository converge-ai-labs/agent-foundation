"""Offline image reuse through Service's actual MCP adapter and send checks."""

from __future__ import annotations

import base64
import json
import re
from contextlib import AsyncExitStack

import pytest
from a13n_harness import RunBindings
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.tools.mcp import DispatchTransport, mcp_capability
from a13n_service.runs.agent import build
from a13n_service.runs.memories.organizer import organizer_config
from fastmcp import Client, FastMCP
from mcp.types import ImageContent
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelRequest, TextContent, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_agents import MODEL, PLUGINS, resolved

pytestmark = pytest.mark.anyio
# A synthetic one-pixel PNG; no private image or external account is involved.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="
)


@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize("direct_input", [False, True])
async def test_native_image_reuse_through_service_mcp(proxy, direct_input) -> None:
    producer, consumer = FastMCP("image-source"), FastMCP("image-sink")
    calls, checks, received = [], [], []

    @producer.tool()
    def produce() -> ImageContent:
        calls.append("produce")
        return ImageContent(type="image", data=base64.b64encode(_PNG).decode(), mimeType="image/png")

    @consumer.tool()
    def consume(image: str) -> dict[str, bool]:
        calls.append("consume")
        received.append(base64.b64decode(image, validate=True))
        return {"accepted": True}

    async def check(dispatch):
        checks.append((dispatch.connection_id, dispatch.tool_name, dispatch.tool_call_id))

    step = 0

    async def stream(messages, info):
        nonlocal step
        current, step = step, step + 1
        if current == 0 and not direct_input:
            group, name, args = "producer", "produce", {}
        elif current == (0 if direct_input else 1):
            text = "\n".join(
                value if isinstance(value, str) else value.content
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
                for value in ([part.content] if isinstance(part.content, str) else part.content)
                if isinstance(value, str | TextContent)
            )
            assert base64.b64encode(_PNG).decode() not in text
            references = re.findall(r"image-base64:[0-9a-f]{32}", text)
            assert references
            group, name, args = "consumer", "consume", {"image": references[-1]}
        else:
            yield "done"
            return
        if proxy:
            args = {"group": group, "tool": name, "arguments": args}
            name = "call_tool"
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"mock-call-{current}")}

    async with AsyncExitStack() as stack:
        capabilities = []
        for name, server in (("producer", producer), ("consumer", consumer)):
            client = Client(server)
            client.transport = DispatchTransport(client.transport, name)
            await stack.enter_async_context(client)

            async def open_client(client=client):
                return client

            capabilities.append(mcp_capability(name, open_client, tools=None, check=check))
        sources = (
            (
                ToolSearchCapability(
                    groups={
                        name: ToolSearchGroup(capability, f"Synthetic image {name}")
                        for name, capability in zip(("producer", "consumer"), capabilities, strict=True)
                    }
                ),
            )
            if proxy
            else tuple(capabilities)
        )
        # Even a conversation with memory-only configured toolsets gets Service's default image reuse.
        root = resolved(new_object_id("ap"), new_object_id("apr"), organizer_config(MODEL))
        executable = build(root, capabilities=lambda _: sources, plugins=PLUGINS, instrumentation=None)

        async def model_resolver(context, model_id):
            return FunctionModel(stream_function=stream)

        result = await executable.run(
            [BinaryContent(_PNG, media_type="image/png")] if direct_input else "copy image",
            bindings=RunBindings.embedded(model_resolver=model_resolver),
        )
    assert result.status == "completed"
    assert calls == (["consume"] if direct_input else ["produce", "consume"])
    assert received == [_PNG]
    assert [(connection, tool) for connection, tool, _ in checks] == (
        [("consumer", "consume")] if direct_input else [("producer", "produce"), ("consumer", "consume")]
    )
    assert all(call_id for _, _, call_id in checks)
