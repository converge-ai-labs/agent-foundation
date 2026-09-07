"""Validation and source loading for reusable CodeAct programs."""

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
_RESERVED_AMBIENT_BUILTIN_NAMES = frozenset({"__import__", "compile", "eval", "exec", "input", "open"})
_FORBIDDEN_AMBIENT_MODULES = frozenset({"os", "pathlib", "socket", "subprocess"})


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
        raise ValueError(f"CodeAct program path must end in {_PROGRAM_SUFFIX}")
    raw = await ctx.deps.environment.files.read_bytes(path, length=max_source_bytes + 1)
    if len(raw) > max_source_bytes:
        raise ValueError(f"CodeAct program exceeds max_source_bytes={max_source_bytes}")
    try:
        source = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("CodeAct program must be strict UTF-8") from exc

    validate_program_source(source)
    suffix = f"\n{_RESULT_NAME} = await main({_INPUT_NAME})\n{_RESULT_NAME}\n"
    return ProgramSource(
        path=path,
        source=source,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        executable_source=source + suffix,
    )


def validate_program_source(source: str) -> None:
    """Validate the version-1 ``async main(inputs)`` module contract."""

    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python syntax: {exc.msg} at line {exc.lineno}") from exc

    main_defs = [
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
    ]
    if len(main_defs) != 1 or not isinstance(main_defs[0], ast.AsyncFunctionDef):
        raise ValueError("CodeAct program must define exactly one async function main(inputs)")
    main = main_defs[0]
    args = main.args
    if (
        len(args.posonlyargs) != 0
        or len(args.args) != 1
        or args.args[0].arg != "inputs"
        or args.vararg is not None
        or args.kwonlyargs
        or args.kwarg is not None
        or args.defaults
        or args.kw_defaults
    ):
        raise ValueError("CodeAct program entrypoint must have the exact signature async def main(inputs)")
    if main.decorator_list:
        raise ValueError("CodeAct program main() cannot have decorators")

    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _validate_function_declaration(node)
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Assign):
            if not all(_is_safe_assignment_target(target) for target in node.targets):
                raise ValueError("Module-level assignments require simple name targets")
            if not _is_safe_constant(node.value):
                raise ValueError("Module-level assignments must contain only side-effect-free constants")
            continue
        if isinstance(node, ast.AnnAssign):
            if not _is_safe_assignment_target(node.target) or not _is_safe_annotation(node.annotation):
                raise ValueError("Module-level annotated assignments require simple names and annotations")
            if node.value is not None and not _is_safe_constant(node.value):
                raise ValueError("Module-level assignments must contain only side-effect-free constants")
            continue
        raise ValueError(f"Executable module-level statement {type(node).__name__} is not allowed in a CodeAct program")

    for node in ast.walk(module):
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.arg):
            name = node.arg
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        elif isinstance(node, ast.alias):
            name = node.asname or node.name.split(".", maxsplit=1)[0]
        else:
            name = None
        if name in _RESERVED_NAMES:
            raise ValueError(f"Program uses reserved runtime name {name!r}")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in _RESERVED_AMBIENT_BUILTIN_NAMES:
            raise ValueError(
                f"CodeAct programs cannot reference reserved ambient builtin name {node.id!r}; "
                "use injected CodeAct-eligible tools for host effects"
            )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_names = (
                (alias.name for alias in node.names) if isinstance(node, ast.Import) else ((node.module or ""),)
            )
            for module_name in module_names:
                root = module_name.split(".", maxsplit=1)[0]
                if root in _FORBIDDEN_AMBIENT_MODULES:
                    raise ValueError(
                        f"CodeAct programs cannot import ambient-capability module {root!r}; "
                        "use injected CodeAct-eligible tools for host effects"
                    )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "main":
            raise ValueError("CodeAct program cannot call main() recursively")


def _validate_function_declaration(node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
    if node.name in _RESERVED_NAMES:
        raise ValueError(f"Program uses reserved runtime name {node.name!r}")
    if node.decorator_list:
        raise ValueError(f"Module-level function {node.name!r} cannot have decorators")
    defaults = [*node.args.defaults, *(value for value in node.args.kw_defaults if value is not None)]
    if any(not _is_safe_constant(value) for value in defaults):
        raise ValueError(f"Module-level function {node.name!r} defaults must be side-effect-free constants")
    annotations = [
        *(argument.annotation for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]),
        node.args.vararg.annotation if node.args.vararg is not None else None,
        node.args.kwarg.annotation if node.args.kwarg is not None else None,
        node.returns,
    ]
    if any(annotation is not None and not _is_safe_annotation(annotation) for annotation in annotations):
        raise ValueError(f"Module-level function {node.name!r} annotations must be side-effect-free names or strings")


def _is_safe_assignment_target(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_is_safe_assignment_target(item) for item in node.elts)
    return False


def _is_safe_annotation(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) or (isinstance(node, ast.Constant) and isinstance(node.value, str))


def _is_safe_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_safe_constant(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None and _is_safe_constant(key) and _is_safe_constant(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Not, ast.Invert)):
        return _is_safe_constant(node.operand)
    return False


def program_inputs(value: dict[str, Any] | None) -> dict[str, Any]:
    """Build Monty's eager input binding without interpolating user data into source."""

    return {_INPUT_NAME: value or {}}
