"""Detached IAM observations shared only by one bounded application operation."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from types import CoroutineType
from typing import Any

from .domain import AuthenticatedActor, PrincipalRef


@dataclass(frozen=True, slots=True)
class WorkspaceIdentity:
    id: str
    organization_id: str


@dataclass(frozen=True, slots=True)
class RoleGrant:
    resource_type: str
    resource_id: str
    role_key: str


@dataclass(frozen=True, slots=True)
class PrincipalAuthorization:
    workspace: WorkspaceIdentity
    bindings: tuple[RoleGrant, ...]


@dataclass(slots=True)
class _Operation:
    principals: dict[tuple[PrincipalRef, str], PrincipalAuthorization] = field(default_factory=dict)
    credentials: set[AuthenticatedActor] = field(default_factory=set)
    ordinary_agents: set[str] = field(default_factory=set)
    active: bool = True


_current: ContextVar[_Operation | None] = ContextVar("iam_operation", default=None)


def current_operation() -> _Operation | None:
    operation = _current.get()
    return operation if operation is not None and operation.active else None


@contextmanager
def authorization_scope() -> Iterator[None]:
    """Share facts across nested calls; never retain sessions or cross operation boundaries.

    Each Principal/Workspace and credential is checked on first use. Later action
    checks reuse immutable observations, including across the operation's external
    I/O. Different credentials, Principals and Workspaces remain independent.
    """
    if current_operation() is not None:
        yield
        return
    operation = _Operation()
    token = _current.set(operation)
    try:
        yield
    finally:
        # A background task may have inherited the context; it cannot retain an
        # earlier request's authority after that request finishes.
        operation.active = False
        operation.principals.clear()
        operation.credentials.clear()
        operation.ordinary_agents.clear()
        _current.reset(token)


def authorization_operation[**P, R](
    function: Callable[P, CoroutineType[Any, Any, R]],
) -> Callable[P, CoroutineType[Any, Any, R]]:
    """Give one non-streaming command/read its own request-local IAM observations."""

    @wraps(function)
    async def invoke(*args: P.args, **kwargs: P.kwargs) -> R:
        with authorization_scope():
            return await function(*args, **kwargs)

    return invoke
