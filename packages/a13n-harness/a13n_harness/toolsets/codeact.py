"""Typed CodeAct eligibility and restricted runner Toolset."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import keyword
import re
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import partial
from types import MappingProxyType
from typing import Annotated, Any, Protocol, Self, cast, runtime_checkable
from uuid import uuid4

from pydantic import Field, JsonValue, ValidationError
from pydantic_ai import FunctionToolset, RunContext, Tool, ToolDefinition, ToolReturn
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ToolFailed, UsageLimitExceeded, UserError
from pydantic_ai.function_signature import FunctionSignature
from pydantic_ai.messages import CachePoint, InstructionPart, TextContent, ToolCallPart, UserContent
from pydantic_ai.tools import ToolDenied
from pydantic_ai.toolsets import AbstractToolset, PrefixedToolset, ToolsetTool, WrapperToolset
from pydantic_ai.usage import RunUsage
from pydantic_monty import (
    MontyConversionError,
    MontyCrashedError,
    MontyRuntimeError,
    MontySyntaxError,
    MontyTypingError,
)

from a13n_harness._review_context import record_approval_denials
from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.codeact.executor import is_sandbox_panic
from a13n_harness.codeact.programs import (
    load_program_source,
    program_inputs,
)
from a13n_harness.codeact.runtime import CodeActExecution, CodeActRunState
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
from a13n_harness.tools.tool_proxy import proxy_control, proxy_membership, resolve_proxy_call

from ._instructions import tool_instruction

_RUN_CODE = "run_code"
_RUN_PROGRAM = "run_program"
_RESERVED_TOOL_NAMES = frozenset({_RUN_CODE, _RUN_PROGRAM})
_INVALID_IDENT_CHARS = re.compile(r"[^a-zA-Z0-9_]")
_CODEACT_CONTRACT_VERSION = 1
_MAX_DIAGNOSTIC_BYTES = 4096

_RUN_CODE_DESCRIPTION = "\n\n".join((tool_instruction("run_code"), tool_instruction("codeact")))
_RUN_PROGRAM_DESCRIPTION = "\n\n".join((tool_instruction("run_program"), tool_instruction("codeact")))


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

    async def admit(self, ctx: RunContext[AgentContext]) -> int:
        async with self.lock:
            while True:
                if self.admitted >= self.config.max_tool_calls:
                    raise RuntimeError(f"CodeAct nested tool-call limit ({self.config.max_tool_calls}) exceeded")
                # Successful usage can already include pending callbacks that are
                # still in after-execution hooks. If the conservative reservation
                # cannot fit, wait for those callbacks to settle and recheck.
                projected = _copy_usage(ctx.usage)
                projected.tool_calls += 2 + self.pending
                try:
                    if ctx.usage_limits is not None:
                        ctx.usage_limits.check_before_tool_call(projected)
                except UsageLimitExceeded:
                    if not self.pending:
                        raise
                    await self.lock.wait()
                else:
                    break
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
        wrapped_tools = cast(dict[str, ToolsetTool[AgentContext]], await self.wrapped.get_tools(ctx))
        conflicts = _RESERVED_TOOL_NAMES.intersection(wrapped_tools)
        if conflicts:
            raise UserError(f"CodeAct reserved tool name conflict: {', '.join(sorted(conflicts))}")
        # Build once during preparation so collisions and invalid schemas fail before
        # either runner is exposed. Execution rebuilds from the active manager.
        _build_catalog(wrapped_tools)
        tools: list[Tool[AgentContext]] = []
        if self.config.inline:
            tools.append(
                Tool(
                    self._run_code,
                    name=_RUN_CODE,
                    description=_RUN_CODE_DESCRIPTION,
                    sequential=True,
                    metadata={"a13n.codeact.runner": True, "code_arg_name": "code", "code_arg_language": "python"},
                )
            )
        if self.config.programs:
            tools.append(
                Tool(
                    self._run_program,
                    name=_RUN_PROGRAM,
                    description=_RUN_PROGRAM_DESCRIPTION,
                    sequential=True,
                    metadata={"a13n.codeact.runner": True, "a13n.codeact.program": True},
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
        catalog = _build_catalog(active_tools)
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
            return await budget.admit(ctx)

        async def dispatch(sandbox_name: str, kwargs: dict[str, Any], ordinal: int) -> Any:
            canonical_name = catalog.sandbox_to_canonical.get(sandbox_name, sandbox_name)
            prepared = catalog.prepared_tools[canonical_name]
            if proxy_control(prepared.tool_def) == "call":
                canonical_name, kwargs = resolve_proxy_call(active_tools, kwargs)
                prepared = active_tools[canonical_name]
                if not is_codeact_tool_eligible(canonical_name, prepared):
                    raise RuntimeError(
                        f"Tool {canonical_name!r} is not enabled for CodeAct. "
                        "Use an ordinary proxy call outside the runner, or ask the tool owner to enable its CodeAct policy."
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
                    result = await _unwrap_tool_return(result, ordinal=ordinal, budget=budget)
                    result_bytes = bounded_json_size(result, self.config.max_output_bytes)
                    await budget.add_bytes(result_bytes, label="nested result")
                    record.result_bytes = result_bytes
                    record.outcome = "completed"
                    return result
            except ValidationError as exc:
                raise TypeError(_format_validation_error(exc)) from None
            except (ApprovalRequired, CallDeferred) as exc:
                record.outcome = "deferred"
                record.error_type = type(exc).__name__
                raise RuntimeError(f"Tool {canonical_name!r} requires unresolved deferred host interaction") from None
            except asyncio.CancelledError as exc:
                record.outcome = "cancelled"
                record.error_type = type(exc).__name__
                raise
            except Exception as exc:
                record.outcome = "failed"
                record.error_type = type(exc).__name__
                raise RuntimeError(f"Nested tool {canonical_name!r} failed with {type(exc).__name__}") from None
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
                raise RuntimeError(f"Tool {canonical_name!r} was denied: {denial.message}")
            raise AssertionError("CodeAct nested dispatch produced no result")

        sequential_names = {name for name, definition in catalog.definitions.items() if definition.sequential}
        global_sequential = manager.get_parallel_execution_mode() == "sequential"
        try:
            if inputs is not None:
                input_size = bounded_json_size(inputs, self.config.max_output_bytes)
                await budget.add_bytes(input_size, label="program inputs")
            async with asyncio.timeout(self.config.timeout_seconds):
                if source_path is None:
                    execution = await self.state.execute_inline(
                        code,
                        dispatch=dispatch,
                        admit=admit,
                        release=budget.release,
                        valid_names=set(catalog.definitions),
                        sequential_names=sequential_names,
                        global_sequential=global_sequential,
                        restart=restart,
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
            try:
                result = await self._success_result(execution, budget=budget)
            except BaseException:
                if source_path is None:
                    await self.state.reset_inline()
                raise
            status = "completed"
            return result
        except asyncio.CancelledError as exc:
            status = "cancelled"
            error_type = type(exc).__name__
            if source_path is None:
                await self.state.reset_inline()
            raise
        except TimeoutError as exc:
            status = "timed_out"
            error_type = type(exc).__name__
            if source_path is None:
                await self.state.reset_inline()
            return self._fail_execution(exc, "CodeAct execution exceeded its timeout", execution_id, budget)
        except (MontySyntaxError, MontyTypingError) as exc:
            error_type = type(exc).__name__
            return self._fail_execution(
                exc, "CodeAct source was rejected by the restricted sandbox", execution_id, budget
            )
        except (MontyCrashedError, MontyConversionError) as exc:
            error_type = type(exc).__name__
            if source_path is None:
                await self.state.reset_inline()
            return self._fail_execution(exc, "CodeAct sandbox failed and inline state was reset", execution_id, budget)
        except MontyRuntimeError as exc:
            error_type = type(exc).__name__
            return self._fail_execution(exc, "CodeAct source failed in the restricted sandbox", execution_id, budget)
        except Exception as exc:
            error_type = type(exc).__name__
            return self._fail_execution(
                exc, f"CodeAct execution failed with {type(exc).__name__}", execution_id, budget
            )
        except BaseException as exc:
            error_type = type(exc).__name__
            if not is_sandbox_panic(exc):
                if source_path is None:
                    await self.state.reset_inline()
                raise
            if source_path is None:
                await self.state.reset_inline()
            return self._fail_execution(exc, "CodeAct sandbox aborted and inline state was reset", execution_id, budget)
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
    def _fail_execution(exc: BaseException, message: str, execution_id: str, budget: _ExecutionBudget) -> Any:
        safe_message = _truncate_utf8(message, _MAX_DIAGNOSTIC_BYTES)
        payload = {
            "codeact": {
                "contract_version": _CODEACT_CONTRACT_VERSION,
                "status": "failed",
                "execution_id": execution_id,
                "tool_call_count": budget.started,
                "cumulative_bytes": budget.cumulative_bytes,
                "side_effect_uncertain": budget.started > 0,
            },
            "error": {"type": type(exc).__name__, "message": safe_message},
        }
        raise ToolFailed(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) from None


def is_codeact_tool_eligible(name: str, tool: ToolsetTool[AgentContext]) -> bool:
    """Apply the same owner policy and category exclusions to direct and proxy targets."""
    definition = tool.tool_def
    return (
        resolve_codeact_eligibility(name, tool)
        and definition.kind in {None, "function", "unapproved"}
        and definition.tool_kind is None
        and not definition.defer_loading
        and not definition.unless_native
        and (definition.metadata or {}).get("a13n.codeact.runner") is not True
    )


def _build_catalog(tools: dict[str, ToolsetTool[AgentContext]]) -> _Catalog:
    definitions: dict[str, ToolDefinition] = {}
    sandbox_to_canonical: dict[str, str] = {}
    prepared_tools: dict[str, ToolsetTool[AgentContext]] = {}
    for canonical_name, tool in tools.items():
        definition = tool.tool_def
        if not is_codeact_tool_eligible(canonical_name, tool) or proxy_membership(definition) is not None:
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


def render_codeact_runner_description(
    tools: dict[str, ToolsetTool[AgentContext]],
    *,
    program: bool,
) -> str:
    base = _RUN_PROGRAM_DESCRIPTION if program else _RUN_CODE_DESCRIPTION
    catalog = _build_catalog(tools)
    if not catalog.definitions:
        return base + "\n\nNo host tools are callable from CodeAct in this run step."

    entries: list[str] = []
    for sandbox_name, definition in sorted(catalog.definitions.items()):
        canonical_name = catalog.sandbox_to_canonical[sandbox_name]
        heading = f"### `{sandbox_name}`"
        if canonical_name != sandbox_name:
            heading += f" (tool `{canonical_name}`)"
        details = [heading, "```python\n" + _render_tool_declaration(definition) + "\n```"]
        if definition.description:
            details.append(f"  {definition.description}")
        details.append(
            "  Arguments JSON Schema: "
            + json.dumps(
                definition.parameters_json_schema,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
        if definition.return_schema is not None:
            details.append(
                "  Return JSON Schema: "
                + json.dumps(
                    definition.return_schema,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            )
        entries.append("\n".join(details))
    return base + "\n\nCodeAct callable host tools:\n" + "\n".join(entries)


def _render_tool_declaration(definition: ToolDefinition) -> str:
    """Render documentation from prepared schemas without changing tool semantics."""
    fallback = f"async def {definition.name}(**kwargs: Any) -> Any: ..."
    schema = definition.parameters_json_schema
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    # The upstream signature renderer does not represent open argument maps or
    # omission without a default faithfully. Keep the exact schema for these tools.
    if schema.get("additionalProperties", True) is not False or any(
        name not in required and (not isinstance(prop, dict) or "default" not in prop)
        for name, prop in properties.items()
    ):
        return fallback
    try:
        signature = FunctionSignature.from_schema(
            name=definition.name,
            parameters_schema=schema,
            return_schema=definition.return_schema,
        )
        for name in required:
            if name in signature.params:
                signature.params[name].default = None
        # Each entry is self-contained; qualify its referenced JSON shapes so
        # unrelated tools with identically named models do not imply shared types.
        names = frozenset(ref.name for ref in signature.referenced_types)
        if len(names) != len(signature.referenced_types):
            return fallback  # Input and output schemas reuse a type name for different shapes.
        declarations = FunctionSignature.render_type_definitions([signature], names)
        declarations.append(signature.render("...", is_async=True, conflicting_type_names=names))
        rendered = "\n\n".join(declarations)
        # This validates generated documentation, never model-authored source.
        # Non-identifier JSON property names remain callable through **mapping.
        ast.parse(rendered)
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return fallback
    return rendered


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


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["Nested tool argument validation failed:"]
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in error.get("loc", ())) or "arguments"
        lines.append(f"- {location}: [{error.get('type', 'validation_error')}]")
    return "\n".join(lines)


def _truncate_utf8(value: str, limit: int) -> str:
    raw = value.encode("utf-8")
    return value if len(raw) <= limit else raw[:limit].decode("utf-8", errors="ignore")


def _copy_usage(usage: RunUsage) -> RunUsage:
    return deepcopy(usage)


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
