"""Source loading and entrypoint checks for reusable Monty programs."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from pydantic_ai import RunContext

from a13n_harness.context import AgentContext

_INPUT_NAME = "__a13n_codeact_inputs__"
_RESULT_NAME = "__a13n_codeact_result__"
_RESERVED_NAMES = frozenset({_INPUT_NAME, _RESULT_NAME})
_PROGRAM_SUFFIX = ".codeact.py"


class ProgramValidationError(ValueError):
    """An authoring error whose message contains no source or input values."""

    def __init__(self, message: str, *, line: int | None = None, column: int | None = None) -> None:
        super().__init__(message)
        self.line = line
        self.column = column


@dataclass(frozen=True)
class ProgramSource:
    path: str
    source: str
    source_sha256: str
    executable_source: str


async def load_program_source(
    ctx: RunContext[AgentContext],
    path: str,
    *,
    max_source_bytes: int,
) -> ProgramSource:
    """Read exact bounded bytes through the current Environment FileOperator."""
    if not PurePath(path).name.endswith(_PROGRAM_SUFFIX):
        raise ProgramValidationError(f"CodeAct program path must end in {_PROGRAM_SUFFIX}")
    raw = await ctx.deps.environment.files.read_bytes(path, length=max_source_bytes + 1)
    if len(raw) > max_source_bytes:
        raise ProgramValidationError(f"CodeAct program exceeds max_source_bytes={max_source_bytes}")
    try:
        source = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProgramValidationError("CodeAct program must be strict UTF-8") from exc

    validate_program_source(source)
    suffix = f"\n{_RESULT_NAME} = await main({_INPUT_NAME})\n{_RESULT_NAME}\n"
    return ProgramSource(
        path=path,
        source=source,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        executable_source=source + suffix,
    )


def validate_program_source(source: str) -> None:
    """Check the entrypoint convention; Monty owns Python execution semantics."""
    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise ProgramValidationError("Invalid Python syntax", line=exc.lineno, column=exc.offset) from None

    main_defs = [
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
    ]
    if len(main_defs) != 1 or not isinstance(main_defs[0], ast.AsyncFunctionDef):
        raise ProgramValidationError("CodeAct program must define exactly one async function main(inputs)")
    main = main_defs[0]
    args = main.args
    if (
        args.posonlyargs
        or len(args.args) != 1
        or args.args[0].arg != "inputs"
        or args.vararg is not None
        or args.kwonlyargs
        or args.kwarg is not None
        or args.defaults
        or args.kw_defaults
        or main.decorator_list
    ):
        raise ProgramValidationError(
            "CodeAct program requires an undecorated async def main(inputs) without defaults",
            line=main.lineno,
            column=main.col_offset + 1,
        )

    for node in ast.walk(module):
        names: tuple[str, ...] = ()
        if isinstance(node, ast.Name):
            names = (node.id,)
        elif isinstance(node, ast.arg):
            names = (node.arg,)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = (node.name,)
        elif isinstance(node, ast.alias):
            names = (node.asname or node.name.split(".", maxsplit=1)[0],)
        elif isinstance(node, ast.ExceptHandler) and node.name is not None:
            names = (node.name,)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names = tuple(node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            names = (node.name,)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            names = (node.rest,)
        if _RESERVED_NAMES.intersection(names):
            raise ProgramValidationError("Program uses a reserved CodeAct runtime name")


def program_inputs(value: dict[str, Any] | None) -> dict[str, Any]:
    """Build Monty's eager input binding without interpolating user data into source."""
    return {_INPUT_NAME: value or {}}
