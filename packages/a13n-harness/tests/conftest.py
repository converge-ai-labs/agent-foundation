from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest
from a13n_harness import AgentSpec, HarnessBuilder
from PIL import Image
from pydantic_ai.models.function import FunctionModel


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def symlink():
    """Create a real link, skipping only for a local Windows privilege failure."""

    def create(link: Path, target: str | Path, *, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as exc:
            if sys.platform == "win32" and exc.winerror == 1314 and os.environ.get("CI", "").lower() != "true":
                pytest.skip("Windows process lacks symlink privilege (enable Developer Mode or run elevated)")
            raise

    return create


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
