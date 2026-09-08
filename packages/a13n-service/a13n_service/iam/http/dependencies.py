"""Shared identity HTTP dependencies and private response policy."""

from typing import Annotated

from fastapi import Depends, Query, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.request_runtime import get_control_runtime

from ..domain import AuthenticatedActor
from ..management.collections import PageRequest
from ..runtime import IdentityRuntime
from ..service_common import identity_error
from .authentication import authenticate_request


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
Pagination = Annotated[PageRequest, Query()]


def identity(request: Request) -> IdentityRuntime:
    control = get_control_runtime(request)
    if control is None or control.identity is None:
        raise identity_error("identity_unavailable", "Identity management is unavailable.", ErrorCategory.unavailable)
    return control.identity
