"""Model-facing Memory tools composed by MemoryCapability."""

from __future__ import annotations

from typing import Annotated, cast

from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._json import dump_json_bytes
from a13n_harness.capabilities.memory import MemoryScope, _MemoryBinding, _normalize_memories
from a13n_harness.context import AgentContext
from a13n_harness.errors import RunError
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import tool_failure

_MEMORY_INSTRUCTION = tool_instruction("memory")
_MAX_QUERY_CHARS = 16_000
_MAX_MEMORY_TEXT_CHARS = 8_000
_MAX_RESULT_BYTES = 240 * 1024


class MemoryToolset:
    """Bounded search, list, and explicit-add tools for one run-local Memory binding."""

    def __init__(self, binding: _MemoryBinding) -> None:
        if not isinstance(binding, _MemoryBinding):
            raise TypeError("binding must be a run-local Memory binding")
        self._binding = binding

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        fixed = self._binding.fixed_scope is not None
        search = self.memory_search_fixed if fixed else self.memory_search
        list_memories = self.memory_list_fixed if fixed else self.memory_list
        add = self.memory_add_fixed if fixed else self.memory_add
        return InstructionFunctionToolset(
            tools=[
                self._tool(search, tool_id="memory.search", name="memory_search", write=False),
                self._tool(list_memories, tool_id="memory.list", name="memory_list", write=False),
                self._tool(add, tool_id="memory.add", name="memory_add", write=True),
            ],
            id="a13n-memory-tools",
            instructions=[_MEMORY_INSTRUCTION],
        )

    @staticmethod
    def _tool(function, *, tool_id: str, name: str, write: bool) -> HarnessTool:
        return HarnessTool(
            function,
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset({"write", "external_communication"} if write else {"read", "external_communication"}),
                credential_audiences=(),
                idempotency="none" if write else "read_only",
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=256 * 1024,
                    max_output_bytes=256 * 1024,
                    overflow="truncate",
                    redact=True,
                ),
            ),
            name=name,
        )

    async def memory_search_fixed(
        self,
        ctx: RunContext[AgentContext],
        query: Annotated[str, Field(min_length=1, max_length=_MAX_QUERY_CHARS, description="Memory search query")],
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum memories to return")] = 5,
    ) -> dict[str, JsonValue]:
        del ctx
        return await self._search(query, scope=None, limit=limit)

    async def memory_search(
        self,
        ctx: RunContext[AgentContext],
        query: Annotated[str, Field(min_length=1, max_length=_MAX_QUERY_CHARS, description="Memory search query")],
        scope: Annotated[MemoryScope, Field(description="Trusted memory scope")],
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum memories to return")] = 5,
    ) -> dict[str, JsonValue]:
        del ctx
        return await self._search(query, scope=scope, limit=limit)

    async def memory_list_fixed(
        self,
        ctx: RunContext[AgentContext],
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum memories to return")] = 20,
    ) -> dict[str, JsonValue]:
        del ctx
        return await self._list(scope=None, limit=limit)

    async def memory_list(
        self,
        ctx: RunContext[AgentContext],
        scope: Annotated[MemoryScope, Field(description="Trusted memory scope")],
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum memories to return")] = 20,
    ) -> dict[str, JsonValue]:
        del ctx
        return await self._list(scope=scope, limit=limit)

    async def memory_add_fixed(
        self,
        ctx: RunContext[AgentContext],
        text: Annotated[
            str,
            Field(min_length=1, max_length=_MAX_MEMORY_TEXT_CHARS, description="Exact memory text to store"),
        ],
    ) -> dict[str, JsonValue]:
        del ctx
        return await self._add(text, scope=None)

    async def memory_add(
        self,
        ctx: RunContext[AgentContext],
        text: Annotated[
            str,
            Field(min_length=1, max_length=_MAX_MEMORY_TEXT_CHARS, description="Exact memory text to store"),
        ],
        scope: Annotated[MemoryScope, Field(description="Trusted memory scope")],
    ) -> dict[str, JsonValue]:
        del ctx
        return await self._add(text, scope=scope)

    async def _search(
        self,
        query: str,
        *,
        scope: MemoryScope | None,
        limit: int,
    ) -> dict[str, JsonValue]:
        try:
            memories = await self._binding.search(query, scope=scope, limit=limit)
            return _memory_result(_normalize_memories(memories, limit=limit))
        except TimeoutError:
            return _failure("memory_timeout", retry_hint="retry")
        except RunError as exc:
            return cast(
                dict[str, JsonValue], tool_failure(exc.code, str(exc), details=exc.details, retry_hint=exc.retry_hint)
            )
        except (TypeError, ValueError):
            return _failure("memory_response_invalid")
        except Exception:
            return _failure("memory_search_failed", retry_hint="retry")

    async def _list(
        self,
        *,
        scope: MemoryScope | None,
        limit: int,
    ) -> dict[str, JsonValue]:
        try:
            page = await self._binding.list(scope=scope, limit=limit)
            return _memory_result(_normalize_memories(page.items, limit=limit))
        except TimeoutError:
            return _failure("memory_timeout", retry_hint="retry")
        except RunError as exc:
            return cast(
                dict[str, JsonValue], tool_failure(exc.code, str(exc), details=exc.details, retry_hint=exc.retry_hint)
            )
        except (TypeError, ValueError):
            return _failure("memory_response_invalid")
        except Exception:
            return _failure("memory_list_failed", retry_hint="retry")

    async def _add(self, text: str, *, scope: MemoryScope | None) -> dict[str, JsonValue]:
        try:
            await self._binding.add(text, scope=scope)
            return {"ok": True, "added": True}
        except TimeoutError:
            return _failure("memory_write_unconfirmed")
        except RunError as exc:
            return cast(
                dict[str, JsonValue], tool_failure(exc.code, str(exc), details=exc.details, retry_hint=exc.retry_hint)
            )
        except (TypeError, ValueError):
            return _failure("memory_write_unconfirmed")
        except Exception:
            return _failure("memory_write_unconfirmed")


def _memory_result(memories) -> dict[str, JsonValue]:
    projected: list[dict[str, JsonValue]] = []
    for memory in memories:
        candidate = [*projected, memory.as_json()]
        payload: dict[str, JsonValue] = {
            "ok": True,
            "memories": cast(JsonValue, candidate),
            "count": len(candidate),
            "truncated": len(candidate) < len(memories),
        }
        if len(dump_json_bytes(payload)) > _MAX_RESULT_BYTES:
            break
        projected = candidate
    return {
        "ok": True,
        "memories": cast(JsonValue, projected),
        "count": len(projected),
        "truncated": len(projected) < len(memories),
    }


def _failure(code: str, *, retry_hint: str | None = None) -> dict[str, JsonValue]:
    message = {
        "memory_timeout": "The memory operation timed out.",
        "memory_response_invalid": "The memory provider returned an invalid response.",
        "memory_scope_unavailable": "The requested memory scope is unavailable.",
        "memory_search_failed": "The memory search failed; check the configured provider.",
        "memory_list_failed": "The memory list operation failed; check the configured provider.",
        "memory_write_unconfirmed": "The memory write could not be confirmed. Inspect current memory before repeating the write.",
    }.get(code, "The memory operation could not complete.")
    return cast(dict[str, JsonValue], tool_failure(code, message, retry_hint=retry_hint))


__all__: list[str] = []
