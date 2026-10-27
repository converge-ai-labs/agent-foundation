from __future__ import annotations

import io

import pytest
from a13n_harness import AgentSpec, HarnessBuilder
from PIL import Image
from pydantic_ai.models.function import FunctionModel


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def png_image_bytes() -> bytes:
    with Image.new("RGB", (16, 16), "red") as image:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
async def reviewer_context():
    async def unused_model(messages, info):
        yield "unused"

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=unused_model))
    async with executable.stream("context fixture") as stream:
        yield stream.context
