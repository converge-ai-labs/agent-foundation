"""Silent text append limits of realtime model adapters."""

from collections.abc import Iterator
from functools import singledispatch

from pydantic_ai.realtime import RealtimeModel
from pydantic_ai.realtime.openai_live import OpenAILiveModel


@singledispatch
def context_chunks(model: RealtimeModel, text: str) -> Iterator[str]:
    yield text


@context_chunks.register
def _(model: OpenAILiveModel, text: str) -> Iterator[str]:
    # Each GPT-Live thinking append accepts 500 tokens. UTF-8 bytes bound its tokenizer conservatively.
    data = text.encode()
    while data:
        chunk = data[:500].decode(errors="ignore")
        yield chunk
        data = data[len(chunk.encode()) :]
