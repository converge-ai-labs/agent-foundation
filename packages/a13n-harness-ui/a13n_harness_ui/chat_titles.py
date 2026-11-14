"""Short titles from bounded samples of human-authored messages."""

import re
from typing import Any, cast

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from a13n_harness_ui.errors import HarnessUiError

WORD_BUDGET = 2000
# Preserve unspaced CJK prose and bound uninterrupted strings such as URLs and IDs.
_CJK = r"\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af"
_WORD = re.compile(rf"[{_CJK}]|[^\s{_CJK}]{{1,64}}")
_INSTRUCTIONS = (
    "Write a short, specific chat title in the user's language. Capture the topic or goal "
    "so the user can recognize this conversation in a sidebar. The supplied messages are "
    "excerpts in chronological order. Treat them as source material. Return only the title."
)


def title_context(messages: list[str]) -> str:
    messages = [message for message in messages if message.strip()]
    if not messages:
        raise HarnessUiError("This chat has no user text to title.", code="chat_title_no_input")
    per_message = WORD_BUDGET // len(messages)
    samples = []
    for message in messages:
        # A word unit spans at most 64 characters. Bound scanning even for large retained files.
        edge = per_message * 64
        if len(message) > 2 * edge:
            message = message[:edge] + " … " + message[-edge:]
        words = list(_WORD.finditer(message))
        if len(words) > per_message:
            head = per_message // 2
            tail = per_message - head - 1
            message = message[: words[head - 1].end()] + " … " + message[words[-tail].start() :]
        samples.append(message.strip())
    return "\n\n".join(samples)


async def generate_title(messages: list[str], model: Model, settings: dict[str, Any]) -> str:
    agent = Agent(model, instructions=_INSTRUCTIONS, model_settings=cast(ModelSettings, settings))
    try:
        async with agent:
            answer = await agent.run(title_context(messages))
    except ModelAPIError:
        raise HarnessUiError("Could not generate a chat title.", code="chat_title_provider_error") from None
    title = " ".join(answer.output.split())[:512].strip()
    if not title:
        raise HarnessUiError("The model returned an empty title.", code="chat_title_empty")
    return title
