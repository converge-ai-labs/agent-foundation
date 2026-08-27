"""Validation and source loading for reusable CodeAct programs."""

from __future__ import annotations

import ast
import builtins
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


class _ScopeBindings(ast.NodeVisitor):
    """Collect bindings in one lexical scope without entering child scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_comprehension(self, node: ast.comprehension) -> None:
        return

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".", maxsplit=1)[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)


class _DirectCallVisitor(ast.NodeVisitor):
    """Check direct calls in one expression/statement without child scopes."""

    def __init__(self, allowed: set[str], missing: set[str]) -> None:
        self.allowed = allowed
        self.missing = missing

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id not in self.allowed:
            self.missing.add(node.func.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        arguments = {
            argument.arg
            for argument in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
        }
        if node.args.vararg is not None:
            arguments.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            arguments.add(node.args.kwarg.arg)
        _DirectCallVisitor(self.allowed | arguments, self.missing).visit(node.body)


class _StaticCallAnalyzer:
    def __init__(
        self,
        base_names: set[str],
        module_bindings: set[str],
        *,
        functions_see_complete_module: bool,
    ) -> None:
        self.base_names = base_names
        self.module_bindings = module_bindings
        self.function_globals = module_bindings if functions_see_complete_module else set()
        self.missing: set[str] = set()

    def analyze(self, module: ast.Module) -> set[str]:
        self._analyze_block(
            module.body,
            current=set(self.base_names),
            fallback=set(self.base_names),
            local_names=set(self.module_bindings),
        )
        return self.missing

    def _allowed(self, current: set[str], fallback: set[str], local_names: set[str]) -> set[str]:
        return current | (fallback - local_names)

    def _check(self, node: ast.AST | None, allowed: set[str]) -> None:
        if node is not None:
            _DirectCallVisitor(allowed, self.missing).visit(node)

    def _analyze_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        closure_names: set[str],
    ) -> None:
        bindings = _ScopeBindings()
        for statement in node.body:
            bindings.visit(statement)
        local_names = bindings.names - bindings.global_names - bindings.nonlocal_names
        arguments = {
            argument.arg
            for argument in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
        }
        if node.args.vararg is not None:
            arguments.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            arguments.add(node.args.kwarg.arg)
        self._analyze_block(
            node.body,
            current=arguments,
            fallback=closure_names | self.function_globals,
            local_names=local_names,
        )

    def _analyze_block(
        self,
        statements: list[ast.stmt],
        *,
        current: set[str],
        fallback: set[str],
        local_names: set[str],
    ) -> set[str]:
        current = set(current)
        for statement in statements:
            allowed = self._allowed(current, fallback, local_names)
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for expression in [
                    *statement.decorator_list,
                    *statement.args.defaults,
                    *(value for value in statement.args.kw_defaults if value is not None),
                    *(
                        argument.annotation
                        for argument in [
                            *statement.args.posonlyargs,
                            *statement.args.args,
                            *statement.args.kwonlyargs,
                        ]
                    ),
                    statement.args.vararg.annotation if statement.args.vararg is not None else None,
                    statement.args.kwarg.annotation if statement.args.kwarg is not None else None,
                    statement.returns,
                ]:
                    self._check(expression, allowed)
                current.add(statement.name)
                self._analyze_function(statement, closure_names=allowed | {statement.name})
            elif isinstance(statement, ast.ClassDef):
                for expression in [*statement.decorator_list, *statement.bases, *statement.keywords]:
                    self._check(expression, allowed)
                current.add(statement.name)
                self._analyze_block(
                    statement.body,
                    current=set(),
                    fallback=allowed | self.function_globals,
                    local_names=_statement_bindings(statement.body),
                )
            elif isinstance(statement, (ast.Import, ast.ImportFrom)):
                current.update(_statement_bindings([statement]))
            elif isinstance(statement, ast.Assign):
                self._check(statement.value, allowed)
                for target in statement.targets:
                    self._check(target, allowed)
                    current.update(_assignment_target_names(target))
            elif isinstance(statement, ast.AnnAssign):
                self._check(statement.annotation, allowed)
                self._check(statement.value, allowed)
                self._check(statement.target, allowed)
                current.update(_assignment_target_names(statement.target))
            elif isinstance(statement, ast.AugAssign):
                self._check(statement.target, allowed)
                self._check(statement.value, allowed)
                current.update(_assignment_target_names(statement.target))
            elif isinstance(statement, ast.If):
                self._check(statement.test, allowed)
                body = self._analyze_block(
                    statement.body,
                    current=current,
                    fallback=fallback,
                    local_names=local_names,
                )
                alternate = self._analyze_block(
                    statement.orelse,
                    current=current,
                    fallback=fallback,
                    local_names=local_names,
                )
                current = body & alternate
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                self._check(statement.iter, allowed)
                loop_current = current | _assignment_target_names(statement.target)
                self._analyze_block(
                    statement.body,
                    current=loop_current,
                    fallback=fallback,
                    local_names=local_names,
                )
                self._analyze_block(
                    statement.orelse,
                    current=current,
                    fallback=fallback,
                    local_names=local_names,
                )
            elif isinstance(statement, ast.While):
                self._check(statement.test, allowed)
                self._analyze_block(
                    statement.body,
                    current=current,
                    fallback=fallback,
                    local_names=local_names,
                )
                self._analyze_block(
                    statement.orelse,
                    current=current,
                    fallback=fallback,
                    local_names=local_names,
                )
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                self._analyze_block(
                    statement.body,
                    current=current,
                    fallback=fallback,
                    local_names=local_names,
                )
                for handler in statement.handlers:
                    self._check(handler.type, allowed)
                    handler_current = set(current)
                    if handler.name is not None:
                        handler_current.add(handler.name)
                    self._analyze_block(
                        handler.body,
                        current=handler_current,
                        fallback=fallback,
                        local_names=local_names,
                    )
                self._analyze_block(
                    statement.orelse,
                    current=current,
                    fallback=fallback,
                    local_names=local_names,
                )
                self._analyze_block(
                    statement.finalbody,
                    current=current,
                    fallback=fallback,
                    local_names=local_names,
                )
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                body_current = set(current)
                for item in statement.items:
                    self._check(item.context_expr, allowed)
                    if item.optional_vars is not None:
                        body_current.update(_assignment_target_names(item.optional_vars))
                self._analyze_block(
                    statement.body,
                    current=body_current,
                    fallback=fallback,
                    local_names=local_names,
                )
            else:
                self._check(statement, allowed)
                if isinstance(statement, ast.Delete):
                    for target in statement.targets:
                        current.difference_update(_assignment_target_names(target))
        return current


def _statement_bindings(statements: list[ast.stmt]) -> set[str]:
    collector = _ScopeBindings()
    for statement in statements:
        collector.visit(statement)
    return collector.names - collector.global_names - collector.nonlocal_names


def extract_persistent_bound_names(source: str) -> set[str]:
    """Return unconditional module bindings retained after a successful REPL feed."""

    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_assignment_target_names(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(_assignment_target_names(node.target))
    return names


def _assignment_target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return {name for item in node.elts for name in _assignment_target_names(item)}
    return set()


def validate_static_tool_references(
    source: str,
    *,
    valid_tool_names: set[str],
    known_names: set[str] | None = None,
    functions_see_complete_module: bool = False,
) -> None:
    """Reject statically unbound direct function calls before side effects start."""

    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError:
        return
    allowed = set(dir(builtins)) | valid_tool_names
    if known_names is not None:
        allowed.update(known_names)
    module_bindings = extract_persistent_bound_names(source)
    missing = sorted(
        _StaticCallAnalyzer(
            allowed,
            module_bindings,
            functions_see_complete_module=functions_see_complete_module,
        ).analyze(module)
    )
    if missing:
        rendered = ", ".join(repr(name) for name in missing)
        raise ValueError(f"CodeAct source references unavailable function names: {rendered}")


def program_inputs(value: dict[str, Any] | None) -> dict[str, Any]:
    """Build Monty's eager input binding without interpolating user data into source."""

    return {_INPUT_NAME: value or {}}
