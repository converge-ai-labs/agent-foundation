"""Run the minimal Agent application with a deterministic offline model."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator

from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .application import run_general_agent

_DEFAULT_PROMPT = "Explain what this Agent application demonstrates."


def create_demo_model() -> FunctionModel:
    """Return a deterministic replacement for the external model provider."""

    async def respond(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages, info
        yield "This normal Python application built and ran an Agent through Agent Harness."

    return FunctionModel(stream_function=respond)


async def _run(prompt: str) -> None:
    result = await run_general_agent(
        prompt,
        model=create_demo_model(),
    )
    print(f"output: {result.output_or_raise()}")
    print(f"thread_id: {result.thread_id}")
    print(f"run_id: {result.run_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default=_DEFAULT_PROMPT)
    arguments = parser.parse_args()
    asyncio.run(_run(arguments.prompt))


if __name__ == "__main__":
    main()
