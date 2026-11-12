"""Typed CodeAct eligibility and restricted runner Toolset."""

from __future__ import annotations

import asyncio
import hashlib
import json
import keyword
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from types import MappingProxyType
from typing import Annotated, Any, Protocol, Self, cast, runtime_checkable
from uuid import uuid4

from pydantic import Field, JsonValue, ValidationError
from pydantic_ai import FunctionToolset, RunContext, Tool, ToolDefinition, ToolReturn
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, ToolFailed, ToolRetryError, UserError
from pydantic_ai.messages import CachePoint, InstructionPart, TextContent, ToolCallPart, UserContent
from pydantic_ai.tools import ToolDenied
from pydantic_ai.toolsets import AbstractToolset, PrefixedToolset, ToolsetTool, WrapperToolset
from pydantic_monty import (
    MontyConversionError,
    MontyCrashedError,
    MontyRuntimeError,
    MontySyntaxError,
    MontyTypingError,
)

from a13n_harness._handoff import HANDOFF_TOOL_METADATA_KEY
from a13n_harness._review_context import record_approval_denials
from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.codeact.executor import is_sandbox_panic
from a13n_harness.codeact.programs import (
    ProgramValidationError,
    load_program_source,
    program_inputs,
)
from a13n_harness.codeact.runtime import CodeActExecution, CodeActRunState, InlineSessionBusyError
from a13n_harness.codeact.values import bounded_json_size
from a13n_harness.context import AgentContext
from a13n_harness.environment import EnvironmentError
from a13n_harness.events import (
    CodeActExecutionCompletedPayload,
    CodeActExecutionStartedPayload,
    CodeActToolCallCompletedPayload,
    CodeActToolCallStartedPayload,
    emit_harness_event,
)
from a13n_harness.guidance import guidance_hint, retry_guidance_page
from a13n_harness.tools._deferred_dispatch import FORWARDED_TOOL_KEY, ToolCallForwarder
from a13n_harness.tools.tool_search import proxy_control, proxy_membership

from ._instructions import TOOL_GUIDANCE_KEY, tool_instruction

_RUN_CODE = "run_code"
_RUN_PROGRAM = "run_program"
_RESERVED_TOOL_NAMES = frozenset({_RUN_CODE, _RUN_PROGRAM})
_INVALID_IDENT_CHARS = re.compile(r"[^a-zA-Z0-9_]")
_CODEACT_CONTRACT_VERSION = 1
_MAX_DIAGNOSTIC_BYTES = 4096


@dataclass(frozen=True, slots=True, kw_only=True)
class CodeActToolPolicy:
    """Owner-local deny-by-default CodeAct eligibility policy."""

    default: bool = False
    tools: Mapping[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.default, bool):
            raise TypeError("CodeActToolPolicy.default must be a boolean")
        values = dict(self.tools)
        if not all(isinstance(name, str) and name and isinstance(value, bool) for name, value in values.items()):
            raise TypeError("CodeActToolPolicy.tools must map non-empty names to booleans")
        object.__setattr__(self, "tools", MappingProxyType(values))

    def allows(self, owner_local_name: str) -> bool:
        return self.tools.get(owner_local_name, self.default)


@runtime_checkable
class CodeActEligibilityCarrier(Protocol):
    owner_local_name: str
    codeact_eligible: bool


@runtime_checkable
class CodeActSourceToolCarrier(Protocol):
    source_tool: ToolsetTool[Any]


@runtime_checkable
class CodeActPolicyProvider(Protocol):
    @property
    def codeact_policy(self) -> CodeActToolPolicy: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class _CodeActPolicyTool:
    toolset: AbstractToolset[Any]
    tool_def: ToolDefinition
    max_retries: int
    args_validator: Any
    args_validator_func: Any
    source_tool: ToolsetTool[Any]
    owner_local_name: str
    codeact_eligible: bool


@dataclass
class CodeActPolicyToolset(WrapperToolset[Any]):
    """Attach an explicit typed CodeAct policy to an arbitrary Toolset."""

    policy: CodeActToolPolicy
    reject_unknown_tools: bool = False

    @property
    def codeact_policy(self) -> CodeActToolPolicy:
        return self.policy

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        tools = await self.wrapped.get_tools(ctx)
        if self.reject_unknown_tools:
            unknown = sorted(set(self.policy.tools) - set(tools))
            if unknown:
                raise UserError(f"CodeAct policy references unknown tools: {', '.join(unknown)}")
        prepared = {
            name: _CodeActPolicyTool(
                toolset=self,
                tool_def=tool.tool_def,
                max_retries=tool.max_retries,
                args_validator=tool.args_validator,
                args_validator_func=tool.args_validator_func,
                source_tool=tool,
                owner_local_name=name,
                codeact_eligible=self.policy.allows(name),
            )
            for name, tool in tools.items()
        }
        return cast(dict[str, ToolsetTool[Any]], prepared)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        if not isinstance(tool, _CodeActPolicyTool):
            raise TypeError("CodeActPolicyToolset received a tool it does not own")
        return await self.wrapped.call_tool(name, tool_args, ctx, tool.source_tool)


def resolve_codeact_eligibility(name: str, tool: ToolsetTool[Any]) -> bool:
    """Resolve typed owner policy through transparent prepared-tool wrappers."""
    current = tool
    seen: set[int] = set()
    while True:
        if isinstance(current, CodeActEligibilityCarrier):
            return current.codeact_eligible
        if not isinstance(current, CodeActSourceToolCarrier) or id(current) in seen:
            break
        seen.add(id(current))
        current = current.source_tool

    owner_name = name
    owner: AbstractToolset[Any] = tool.toolset
    while isinstance(owner, PrefixedToolset):
        prefix = owner.prefix + "_"
        if not owner_name.startswith(prefix):
            return False
        owner_name = owner_name.removeprefix(prefix)
        owner = owner.wrapped
    if not isinstance(owner, CodeActPolicyProvider):
        return False
    return owner.codeact_policy.allows(owner_name)


@dataclass
class _Catalog:
    definitions: dict[str, ToolDefinition]
    sandbox_to_canonical: dict[str, str]
    prepared_tools: dict[str, ToolsetTool[AgentContext]]
    fingerprint: str


@dataclass
class _CallRecord:
    call_id: str
    ordinal: int
    canonical_name: str
    sandbox_name: str
    outcome: str = "running"
    argument_bytes: int = 0
    result_bytes: int | None = None
    error_type: str | None = None
    duration_ms: int | None = None


@dataclass
class _ExecutionBudget:
    config: CodeActConfig
    lock: asyncio.Condition = field(default_factory=asyncio.Condition)
    admitted: int = 0
    pending: int = 0
    started: int = 0
    cumulative_bytes: int = 0
    records: list[_CallRecord] = field(default_factory=list)
    supplemental: list[tuple[int, list[UserContent]]] = field(default_factory=list)
    nested_errors: list[dict[str, Any]] = field(default_factory=list)

    def note_error(
        self, ordinal: int, boundary: str, exc: Exception | ToolDenied, message: str, *, hint: str | None = None
    ) -> None:
        # These are observations, including errors caught by sandbox code, not an
        # inferred causal chain. Keep only a small bounded prefix per execution.
        if len(self.nested_errors) < 4:
            self.nested_errors.append(
                {
                    "ordinal": ordinal,
                    "boundary": boundary,
                    "type": _truncate_utf8(type(exc).__name__, 128),
                    "message": _truncate_utf8(message, 256) + ("\n" + hint if hint is not None else ""),
                }
            )

    async def admit(self) -> int:
        async with self.lock:
            if self.admitted >= self.config.max_tool_calls:
                raise RuntimeError(f"CodeAct nested tool-call limit ({self.config.max_tool_calls}) exceeded")
            self.admitted += 1
            self.pending += 1
            return self.admitted

    async def release(self, ordinal: int) -> None:
        del ordinal
        async with self.lock:
            if self.pending <= 0:
                raise RuntimeError("CodeAct nested-call admission was released twice")
            self.pending -= 1
            self.lock.notify_all()

    async def add_bytes(self, size: int, *, label: str) -> None:
        async with self.lock:
            if size > self.config.max_output_bytes or self.cumulative_bytes + size > self.config.max_output_bytes:
                raise RuntimeError(
                    f"CodeAct cumulative values exceed max_output_bytes={self.config.max_output_bytes} while adding {label}"
                )
            self.cumulative_bytes += size

    async def mark_started(self, record: _CallRecord) -> None:
        async with self.lock:
            self.started += 1
            self.records.append(record)

    async def add_supplemental(self, ordinal: int, content: list[UserContent], size: int) -> None:
        await self.add_bytes(size, label="supplemental content")
        async with self.lock:
            self.supplemental.append((ordinal, content))

    def ordered_supplemental(self) -> list[UserContent]:
        return [item for _, content in sorted(self.supplemental) for item in content]


def _runner_descriptions(guidance: str, *, inline: bool, programs: bool) -> dict[str, str]:
    """Select runner sections while delivering the shared rules exactly once."""
    sections: dict[str, tuple[str, str, str]] = {}
    for name, tag in ((_RUN_CODE, "run-code"), (_RUN_PROGRAM, "run-program")):
        before, opening, remaining = guidance.partition(f"<{tag}>")
        body, closing, after = remaining.partition(f"</{tag}>")
        if not opening or not closing:
            raise ValueError(f"CodeAct guidance requires a complete <{tag}> section")
        sections[name] = (before, body, after)

    first_runner = _RUN_CODE if inline else _RUN_PROGRAM
    descriptions: dict[str, str] = {}
    for name, enabled, tag in ((_RUN_CODE, inline, "run-code"), (_RUN_PROGRAM, programs, "run-program")):
        if not enabled:
            continue
        if name == first_runner:
            other = _RUN_PROGRAM if name == _RUN_CODE else _RUN_CODE
            before, _, after = sections[other]
            descriptions[name] = (before + after).strip()
        else:
            _, body, _ = sections[name]
            descriptions[name] = (
                f"<{tag}>{body}</{tag}>\n\nThe shared CodeAct rules are in the {first_runner} description."
            )
    return descriptions


@dataclass
class CodeActToolset(WrapperToolset[AgentContext]):
    """Add restricted runner tools around the current Agent's final Toolset."""

    config: CodeActConfig
    state: CodeActRunState

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        wrapped = await self.wrapped.for_run(ctx)
        return replace(self, wrapped=wrapped)

    async def for_run_step(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        wrapped = await self.wrapped.for_run_step(ctx)
        return self if wrapped is self.wrapped else replace(self, wrapped=wrapped)

    async def __aenter__(self) -> Self:
        await self.wrapped.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        return await self.wrapped.__aexit__(*args)

    async def get_instructions(
        self,
        ctx: RunContext[AgentContext],
    ) -> str | InstructionPart | Sequence[str | InstructionPart] | None:
        return await self.wrapped.get_instructions(ctx)

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        if ctx.deps.tool_execution is not None and ctx.deps._tool_executions.enabled and ctx.deps.codeact_state is None:
            raise UserError("Owned CodeAct execution requires RunBindings.codeact_state.")
        wrapped_tools = cast(dict[str, ToolsetTool[AgentContext]], await self.wrapped.get_tools(ctx))
        conflicts = _RESERVED_TOOL_NAMES.intersection(wrapped_tools)
        if conflicts:
            raise UserError(f"CodeAct reserved tool name conflict: {', '.join(sorted(conflicts))}")
        # Build once during preparation so collisions and invalid schemas fail before
        # either runner is exposed. Execution rebuilds from the active manager.
        _build_catalog(wrapped_tools)
        descriptions = _runner_descriptions(
            tool_instruction("codeact", reference_root=ctx.deps.instruction_reference_root),
            inline=self.config.inline,
            programs=self.config.programs,
        )

        tools: list[Tool[AgentContext]] = []
        if self.config.inline:
            tools.append(
                Tool(
                    self._run_code,
                    name=_RUN_CODE,
                    description=descriptions[_RUN_CODE],
                    sequential=True,
                    metadata={
                        "a13n.codeact.runner": True,
                        TOOL_GUIDANCE_KEY: "codeact",
                        "code_arg_name": "code",
                        "code_arg_language": "python",
                        "a13n.tool-execution": "owned",
                    },
                )
            )
        if self.config.programs:
            tools.append(
                Tool(
                    self._run_program,
                    name=_RUN_PROGRAM,
                    description=descriptions[_RUN_PROGRAM],
                    sequential=True,
                    metadata={
                        "a13n.codeact.runner": True,
                        TOOL_GUIDANCE_KEY: "codeact",
                        "a13n.codeact.program": True,
                        "a13n.tool-execution": "owned",
                    },
                )
            )
        own = await FunctionToolset(tools, id="a13n-codeact").get_tools(ctx)
        return {**wrapped_tools, **cast(dict[str, ToolsetTool[AgentContext]], own)}

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentContext],
        tool: ToolsetTool[AgentContext],
    ) -> Any:
        if name == _RUN_CODE:
            return await self._run_code(ctx, **tool_args)
        if name == _RUN_PROGRAM:
            return await self._run_program(ctx, **tool_args)
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)

    async def _run_code(
        self,
        ctx: RunContext[AgentContext],
        code: Annotated[str, Field(description="Python source to execute in the restricted sandbox.")],
        restart: Annotated[bool, Field(description="Reset run-local REPL state before executing.")] = False,
    ) -> Any:
        raw = code.encode("utf-8")
        if len(raw) > self.config.max_source_bytes:
            raise ToolFailed(f"Code exceeds max_source_bytes={self.config.max_source_bytes}")
        return await self._execute(
            ctx,
            code,
            source_digest=hashlib.sha256(raw).hexdigest(),
            source_path=None,
            restart=restart,
        )

    async def _run_program(
        self,
        ctx: RunContext[AgentContext],
        path: Annotated[str, Field(description="Environment path to a .codeact.py program.")],
        inputs: Annotated[
            dict[str, JsonValue] | None, Field(description="JSON-compatible inputs for main(inputs).")
        ] = None,
    ) -> Any:
        try:
            bounded_json_size(inputs or {}, self.config.max_output_bytes)
            program = await load_program_source(ctx, path, max_source_bytes=self.config.max_source_bytes)
        except ProgramValidationError as exc:
            error: dict[str, Any] = {"type": type(exc).__name__, "message": str(exc), "boundary": "source"}
            if exc.line is not None:
                error["line"] = exc.line
                error["column"] = exc.column
            raise ToolFailed(json.dumps({"error": error})) from None
        except EnvironmentError as exc:
            raise ToolFailed(
                f"CodeAct program could not be read ({exc.code}); choose a readable .codeact.py file "
                "inside an available Environment mount."
            ) from exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ToolFailed(f"CodeAct program could not be loaded or validated ({type(exc).__name__})") from exc
        return await self._execute(
            ctx,
            program.executable_source,
            source_digest=program.source_sha256,
            source_path=program.path,
            restart=False,
            inputs=program_inputs(inputs),
        )

    async def _execute(
        self,
        ctx: RunContext[AgentContext],
        code: str,
        *,
        source_digest: str,
        source_path: str | None,
        restart: bool,
        inputs: dict[str, Any] | None = None,
    ) -> Any:
        manager = ctx.tool_manager
        if manager is None or manager.tools is None:
            raise RuntimeError("CodeAct requires an active final ToolManager")
        active_tools = {
            name: cast(ToolsetTool[AgentContext], tool)
            for name, tool in manager.tools.items()
            if name not in _RESERVED_TOOL_NAMES
        }
        catalog = _build_catalog(active_tools, ctx=ctx)
        execution_id = f"codeact-{uuid4().hex}"
        outer_call_id = ctx.tool_call_id or execution_id
        kind = "program" if source_path is not None else "inline"
        started_at = time.monotonic()
        status = "failed"
        error_type: str | None = None
        budget = _ExecutionBudget(self.config)
        await _emit(
            ctx,
            CodeActExecutionStartedPayload(
                execution_id=execution_id,
                outer_tool_call_id=outer_call_id,
                kind=kind,
                source_digest=source_digest,
                source_path=source_path,
                catalog_fingerprint=catalog.fingerprint,
            ),
        )

        async def admit(_name: str) -> int:
            return await budget.admit()

        async def dispatch(sandbox_name: str, kwargs: dict[str, Any], ordinal: int) -> Any:
            try:
                return await dispatch_tool(sandbox_name, kwargs, ordinal)
            except Exception as exc:
                if not any(item["ordinal"] == ordinal for item in budget.nested_errors):
                    budget.note_error(ordinal, "arguments", exc, "Nested call rejected before dispatch")
                raise

        async def dispatch_tool(sandbox_name: str, kwargs: dict[str, Any], ordinal: int) -> Any:
            canonical_name = catalog.sandbox_to_canonical.get(sandbox_name, sandbox_name)
            prepared = catalog.prepared_tools[canonical_name]
            if proxy_control(prepared.tool_def) == "call":
                forwarder = (prepared.tool_def.metadata or {}).get(FORWARDED_TOOL_KEY)
                if not isinstance(forwarder, ToolCallForwarder):
                    raise RuntimeError("ToolSearch call control is missing its native forwarder")
                canonical_name, kwargs = forwarder.resolve(active_tools, kwargs)
                prepared = active_tools[canonical_name]
                if not is_codeact_tool_eligible(canonical_name, prepared):
                    raise RuntimeError(
                        f"Tool {canonical_name!r} is not enabled for CodeAct. "
                        "Use the tool call outside the runner, or ask the tool owner to enable its CodeAct policy."
                    )
            current = manager.tools.get(canonical_name) if manager.tools is not None else None
            if current is not prepared:
                raise RuntimeError(f"CodeAct tool {canonical_name!r} is no longer the prepared catalog entry")
            argument_bytes = bounded_json_size(kwargs, self.config.max_output_bytes)
            await budget.add_bytes(argument_bytes, label="nested arguments")
            call_id = f"{execution_id}:{ordinal}"
            call = ToolCallPart(tool_name=canonical_name, args=kwargs, tool_call_id=call_id)
            record = _CallRecord(
                call_id=call_id,
                ordinal=ordinal,
                canonical_name=canonical_name,
                sandbox_name=sandbox_name,
                argument_bytes=argument_bytes,
            )
            nested_started: float | None = None

            async def on_validate(args_valid: bool) -> None:
                nonlocal nested_started
                if not args_valid:
                    return
                await budget.mark_started(record)
                await _emit(
                    ctx,
                    CodeActToolCallStartedPayload(
                        execution_id=execution_id,
                        nested_tool_call_id=call_id,
                        ordinal=ordinal,
                        canonical_tool_name=canonical_name,
                        sandbox_tool_name=sandbox_name,
                    ),
                )
                nested_started = time.monotonic()

            from a13n_harness.tools._output import _NESTED_TOOL_EXECUTION

            denial: ToolDenied | None = None
            nested_boundary = "tool_execution"
            nested_token = _NESTED_TOOL_EXECUTION.set(True)
            try:
                result = await manager.handle_call(
                    call,
                    wrap_validation_errors=False,
                    on_validate=on_validate,
                    on_inline_deferred=partial(record_approval_denials, context=ctx.deps),
                )
                if isinstance(result, ToolDenied):
                    denial = result
                    record.outcome = "deferred"
                    record.error_type = type(result).__name__
                else:
                    nested_boundary = "tool_output"
                    result = await _unwrap_tool_return(result, ordinal=ordinal, budget=budget)
                    result_bytes = bounded_json_size(result, self.config.max_output_bytes)
                    await budget.add_bytes(result_bytes, label="nested result")
                    record.result_bytes = result_bytes
                    record.outcome = "completed"
                    return result
            except ValidationError as exc:
                if nested_started is None:
                    message = _format_validation_error(exc, prepared.tool_def.parameters_json_schema)
                    budget.note_error(ordinal, "arguments", exc, message)
                    raise TypeError(message) from None
                budget.note_error(ordinal, nested_boundary, exc, "Nested tool execution failed")
                record.outcome = "failed"
                record.error_type = type(exc).__name__
                raise RuntimeError(f"Nested tool {canonical_name!r} failed with ValidationError") from None
            except (ApprovalRequired, CallDeferred) as exc:
                budget.note_error(ordinal, nested_boundary, exc, "Nested call requires deferred host interaction")
                record.outcome = "deferred"
                record.error_type = type(exc).__name__
                raise RuntimeError(f"Tool {canonical_name!r} requires unresolved deferred host interaction") from None
            except asyncio.CancelledError as exc:
                record.outcome = "cancelled"
                record.error_type = type(exc).__name__
                raise
            except Exception as exc:
                page = retry_guidance_page(exc) if isinstance(exc, ModelRetry | ToolRetryError) else None
                root = ctx.deps.instruction_reference_root
                hint = guidance_hint(root, page) if root is not None and page is not None else None
                budget.note_error(
                    ordinal, nested_boundary, exc, "Nested tool execution or output validation failed", hint=hint
                )
                record.outcome = "failed"
                record.error_type = type(exc).__name__
                message = f"Nested tool {canonical_name!r} failed with {type(exc).__name__}"
                raise RuntimeError(message + ("\n" + hint if hint is not None else "")) from None
            finally:
                _NESTED_TOOL_EXECUTION.reset(nested_token)
                if nested_started is not None:
                    record.duration_ms = max(0, round((time.monotonic() - nested_started) * 1000))
                    await _emit(
                        ctx,
                        CodeActToolCallCompletedPayload(
                            execution_id=execution_id,
                            nested_tool_call_id=call_id,
                            outcome=cast(Any, record.outcome),
                            duration_ms=record.duration_ms,
                            value_bytes=record.result_bytes,
                            error_type=record.error_type,
                            side_effect_uncertain=record.outcome in {"failed", "cancelled", "deferred"},
                        ),
                    )
            if denial is not None:
                budget.note_error(ordinal, nested_boundary, denial, "Nested call was denied")
                raise RuntimeError(f"Tool {canonical_name!r} was denied: {denial.message}")
            raise AssertionError("CodeAct nested dispatch produced no result")

        boundary = "inputs" if inputs is not None else "sandbox"

        async def finish(execution: CodeActExecution) -> ToolReturn:
            nonlocal boundary
            boundary = "final_output"
            return await self._success_result(execution, budget=budget)

        def fail(exc: BaseException, message: str) -> Any:
            inline_state = (
                "unchanged" if source_path is not None or isinstance(exc, InlineSessionBusyError) else "reset"
            )
            return self._fail_execution(
                exc,
                message,
                execution_id,
                budget,
                boundary="source" if isinstance(exc, MontySyntaxError) else boundary,
                inline_state=inline_state,
            )

        sequential_names = {name for name, definition in catalog.definitions.items() if definition.sequential}
        global_sequential = manager.get_parallel_execution_mode() == "sequential"
        try:
            if inputs is not None:
                input_size = bounded_json_size(inputs, self.config.max_output_bytes)
                await budget.add_bytes(input_size, label="program inputs")
            boundary = "sandbox"
            async with asyncio.timeout(self.config.timeout_seconds):
                if source_path is None:
                    result = await self.state.execute_inline(
                        code,
                        dispatch=dispatch,
                        admit=admit,
                        release=budget.release,
                        valid_names=set(catalog.definitions),
                        sequential_names=sequential_names,
                        global_sequential=global_sequential,
                        restart=restart,
                        finish=finish,
                    )
                else:
                    execution = await self.state.execute_program(
                        code,
                        script_name=source_path,
                        inputs=inputs or {},
                        dispatch=dispatch,
                        admit=admit,
                        release=budget.release,
                        valid_names=set(catalog.definitions),
                        sequential_names=sequential_names,
                        global_sequential=global_sequential,
                    )
                    result = await finish(execution)
            status = "completed"
            return result
        except asyncio.CancelledError as exc:
            status = "cancelled"
            error_type = type(exc).__name__
            raise
        except TimeoutError as exc:
            status = "timed_out"
            error_type = type(exc).__name__
            return fail(exc, "CodeAct execution exceeded its timeout")
        except (MontySyntaxError, MontyTypingError) as exc:
            error_type = type(exc).__name__
            return fail(exc, "CodeAct source was rejected by the restricted sandbox")
        except (MontyCrashedError, MontyConversionError) as exc:
            error_type = type(exc).__name__
            return fail(exc, "CodeAct sandbox failed")
        except MontyRuntimeError as exc:
            error_type = type(exc).__name__
            return fail(exc, "CodeAct source failed in the restricted sandbox")
        except Exception as exc:
            error_type = type(exc).__name__
            return fail(exc, f"CodeAct execution failed with {type(exc).__name__}")
        except BaseException as exc:
            error_type = type(exc).__name__
            if not is_sandbox_panic(exc):
                raise
            return fail(exc, "CodeAct sandbox aborted")
        finally:
            await _emit(
                ctx,
                CodeActExecutionCompletedPayload(
                    execution_id=execution_id,
                    status=cast(Any, status),
                    duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
                    call_count=budget.started,
                    cumulative_bytes=budget.cumulative_bytes,
                    error_type=error_type,
                    side_effect_uncertain=status != "completed" and budget.started > 0,
                ),
            )

    async def _success_result(self, execution: CodeActExecution, *, budget: _ExecutionBudget) -> ToolReturn:
        output = execution.completed.output
        if execution.printed and output is not None:
            value: Any = {"output": execution.printed, "result": output}
        elif execution.printed:
            value = {"output": execution.printed}
        else:
            value = output
        size = bounded_json_size(value, self.config.max_output_bytes)
        await budget.add_bytes(size, label="final output")
        return ToolReturn(return_value=value, content=budget.ordered_supplemental() or None)

    @staticmethod
    def _fail_execution(
        exc: BaseException,
        message: str,
        execution_id: str,
        budget: _ExecutionBudget,
        *,
        boundary: str = "sandbox",
        inline_state: str | None = None,
    ) -> Any:
        calls: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {
            "contract_version": _CODEACT_CONTRACT_VERSION,
            "status": "failed",
            "execution_id": execution_id,
            "tool_call_count": budget.started,
            "cumulative_bytes": budget.cumulative_bytes,
            "side_effect_uncertain": budget.started > 0,
            "calls": calls,
            "omitted_call_count": budget.started,
        }
        # Leave room for call identities even when diagnostic text needs JSON escaping.
        payload = {
            "codeact": metadata,
            "error": {
                "type": _truncate_utf8(type(exc).__name__, 128),
                "message": _truncate_utf8(message, 256),
            },
        }
        details: dict[str, Any] = {"boundary": boundary}
        if inline_state is not None:
            details["inline_state"] = inline_state
        if isinstance(exc, (MontySyntaxError, MontyRuntimeError)):
            details["exception_type"] = _truncate_utf8(type(exc.exception()).__name__, 128)
            frames = exc.traceback()
            if frames:
                frame = frames[-1]
                details["location"] = {"line": frame.line, "column": frame.column}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        for key, value in details.items():
            payload["error"][key] = value
            candidate = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(candidate.encode("utf-8")) > 3072:
                del payload["error"][key]
            else:
                encoded = candidate
        observed: list[dict[str, Any]] = []
        for item in sorted(budget.nested_errors, key=lambda item: item["ordinal"]):
            observed.append(item)
            payload["error"]["nested_errors"] = observed
            candidate = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(candidate.encode("utf-8")) > 3072:
                observed.pop()
                break
            encoded = candidate
        for record in sorted(budget.records, key=lambda record: record.ordinal):
            calls.append(
                {
                    "call_id": record.call_id,
                    "ordinal": record.ordinal,
                    "canonical_tool_name": record.canonical_name,
                    "outcome": record.outcome,
                }
            )
            metadata["omitted_call_count"] = budget.started - len(calls)
            candidate = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(candidate.encode("utf-8")) > _MAX_DIAGNOSTIC_BYTES:
                break
            encoded = candidate
        raise ToolFailed(encoded) from None


def is_codeact_tool_eligible(name: str, tool: ToolsetTool[AgentContext]) -> bool:
    """Apply the same owner policy and category exclusions to direct and proxy targets."""
    definition = tool.tool_def
    return (
        resolve_codeact_eligibility(name, tool)
        and definition.kind in {None, "function", "unapproved"}
        and definition.tool_kind is None
        and not definition.unless_native
        and (definition.metadata or {}).get("a13n.codeact.runner") is not True
        and (definition.metadata or {}).get(HANDOFF_TOOL_METADATA_KEY) is not True
    )


def _build_catalog(
    tools: dict[str, ToolsetTool[AgentContext]], *, ctx: RunContext[AgentContext] | None = None
) -> _Catalog:
    # Preparation checks names across all eligible tools. Presentation and execution
    # additionally require current discovery/capability availability, not a cleared
    # defer_loading flag: Pydantic retains that flag after discovery.
    definitions: dict[str, ToolDefinition] = {}
    sandbox_to_canonical: dict[str, str] = {}
    prepared_tools: dict[str, ToolsetTool[AgentContext]] = {}
    for canonical_name, tool in tools.items():
        definition = tool.tool_def
        if not is_codeact_tool_eligible(canonical_name, tool) or proxy_membership(definition) is not None:
            continue
        if ctx is not None and not ctx.is_tool_available(definition):
            continue
        sandbox_name = _sanitize_name(canonical_name)
        if sandbox_name in _RESERVED_TOOL_NAMES:
            raise UserError(f"Tool {canonical_name!r} maps to reserved CodeAct name {sandbox_name!r}")
        if sandbox_name in definitions:
            other = sandbox_to_canonical.get(sandbox_name, sandbox_name)
            raise UserError(f"CodeAct tool-name collision: {other!r} and {canonical_name!r}")
        projected = definition if sandbox_name == canonical_name else replace(definition, name=sandbox_name)
        definitions[sandbox_name] = projected
        sandbox_to_canonical[sandbox_name] = canonical_name
        prepared_tools[canonical_name] = tool
    payload = [
        {
            "contract_version": _CODEACT_CONTRACT_VERSION,
            "sandbox_name": name,
            "canonical_name": sandbox_to_canonical[name],
            "parameters": definition.parameters_json_schema,
            "return": definition.return_schema,
            "sequential": definition.sequential,
        }
        for name, definition in sorted(definitions.items())
    ]
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return _Catalog(definitions, sandbox_to_canonical, prepared_tools, fingerprint)


def render_codeact_catalog(
    tools: dict[str, ToolsetTool[AgentContext]],
    *,
    ctx: RunContext[AgentContext],
    catalog_argument_schemas: bool = False,
) -> str:
    catalog = _build_catalog(tools, ctx=ctx)
    if not catalog.definitions:
        return "No host tools are callable from CodeAct in this run step."

    entries: list[str] = []
    for sandbox_name, definition in sorted(catalog.definitions.items()):
        canonical_name = catalog.sandbox_to_canonical[sandbox_name]
        entry = f"`{sandbox_name}`"
        if canonical_name != sandbox_name:
            entry += f" (tool `{canonical_name}`)"
        if catalog_argument_schemas:
            entry += "\n  Arguments JSON Schema: " + json.dumps(
                definition.parameters_json_schema,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        entries.append(entry)
    return (
        "CodeAct callable host tools. Call them with the same keyword arguments as the matching tool definitions.\n"
        + "\n".join(entries)
    )


def _sanitize_name(name: str) -> str:
    value = _INVALID_IDENT_CHARS.sub("_", name)
    if value and value[0].isdigit():
        value = "_" + value
    if keyword.iskeyword(value):
        value += "_"
    return value or "_"


async def _unwrap_tool_return(result: Any, *, ordinal: int, budget: _ExecutionBudget) -> Any:
    if not isinstance(result, ToolReturn):
        return result
    if result.content is not None:
        if isinstance(result.content, str):
            content: list[UserContent] = [result.content]
        elif isinstance(result.content, Sequence):
            content = list(result.content)
        else:
            raise TypeError("Unsupported ToolReturn supplemental content")
        size = _bounded_content_size(content, budget.config.max_output_bytes)
        await budget.add_supplemental(ordinal, content, size)
    return result.return_value


def _bounded_content_size(value: Sequence[UserContent], limit: int) -> int:
    # Native text metadata and request-control markers need bounded JSON
    # budget representations; their original values stay outside the sandbox.
    projected = [
        {"content": item.content, "metadata": item.metadata}
        if isinstance(item, TextContent)
        else {"kind": item.kind, "ttl": item.ttl}
        if isinstance(item, CachePoint)
        else item
        for item in value
    ]
    return bounded_json_size(projected, limit, allow_binary=True)


def _format_validation_error(exc: ValidationError, schema: dict[str, Any]) -> str:
    lines = ["Nested tool argument validation failed:"]
    argument_names = schema.get("properties", {})
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        # Deeper locations can contain caller-controlled dictionary keys or union tags.
        # Only retain argument names declared by the prepared tool schema.
        path = error.get("loc", ())
        root = path[0] if path else None
        location = root if isinstance(root, str) and root in argument_names else "arguments"
        lines.append(
            f"- {_truncate_utf8(location, 128)}: [{_truncate_utf8(error.get('type', 'validation_error'), 64)}]"
        )
        if len(lines) >= 5:
            break
    return _truncate_utf8("\n".join(lines), 256)


def _truncate_utf8(value: str, limit: int) -> str:
    raw = value[:limit].encode("utf-8", errors="replace")
    return raw[:limit].decode("utf-8", errors="ignore")


async def _emit(ctx: RunContext[AgentContext], payload: Any) -> None:
    try:
        await emit_harness_event(ctx.deps.events, kind="diagnostic", payload=payload)
    except Exception:
        return


__all__ = [
    "CodeActPolicyToolset",
    "CodeActToolPolicy",
    "CodeActToolset",
    "resolve_codeact_eligibility",
]
