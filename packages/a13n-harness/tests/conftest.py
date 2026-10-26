from __future__ import annotations

import io

import pytest
from a13n_harness import AgentSpec, HarnessBuilder
from PIL import Image
from pydantic_ai.models.function import FunctionModel


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--browser-no-sandbox",
        action="store_true",
        help="Explicitly disable the Chromium sandbox for disposable native test profiles.",
    )
    parser.addoption(
        "--browser-executable",
        metavar="PATH",
        help="Run native browser tests with an installed Chromium executable; no browser is downloaded.",
    )


@pytest.fixture
def browser_executable(request: pytest.FixtureRequest) -> str:
    executable = request.config.getoption("--browser-executable")
    if not executable:
        pytest.skip("Native browser tests require --browser-executable=PATH")
    return executable


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
