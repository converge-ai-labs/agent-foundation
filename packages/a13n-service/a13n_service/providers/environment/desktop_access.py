"""Internal management protocol for a preinstalled, provider-local desktop endpoint.

These operations are not Agent tools or public API grants. The caller must own the
durable target reservation and reauthorize the human before issuing or renewing a grant.
No operation allocates, starts, resumes or installs software in an environment.
"""

from __future__ import annotations

import base64
import shlex
from typing import Annotated, Literal

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.errors import provider_error
from pydantic import BaseModel, ConfigDict, Field, SecretStr

SCRIPT = "/opt/a13n-desktop-access.py"
ROOT = "/run/a13n-desktop-access"
PORT = 6081
Identity = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{7,71}$")]
Generation = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Epoch = Annotated[int, Field(strict=True, ge=1)]
TTL = Annotated[int, Field(strict=True, ge=1, le=60)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RequestModel(Model):
    protocol_version: Literal[1] = 1


class Describe(RequestModel):
    operation: Literal["describe"] = "describe"


class Grant(RequestModel):
    operation: Literal["grant"] = "grant"
    generation: Generation
    id: Identity
    mode: Literal["view", "control"]
    epoch: Epoch
    ttl_seconds: TTL = 60


class Inspect(RequestModel):
    operation: Literal["inspect", "revoke"]
    generation: Generation
    id: Identity
    epoch: Epoch


class Renew(RequestModel):
    operation: Literal["renew"] = "renew"
    generation: Generation
    id: Identity
    epoch: Epoch
    ttl_seconds: TTL = 60


class Settle(RequestModel):
    operation: Literal["settle"] = "settle"
    generation: Generation


type Request = Describe | Grant | Inspect | Renew | Settle


class Description(Model):
    protocol_version: Literal[1]
    generation: Generation
    port: Literal[6081]
    max_ttl_seconds: Literal[60]
    read_only_enforced: Literal[True]
    revocation_fences_grants: bool = False
    websocket_url: str | None = Field(default=None, max_length=2048)


class AccessGrant(Model):
    id: Identity
    generation: Generation
    mode: Literal["view", "control"]
    epoch: Epoch
    state: Literal["active", "revoking", "revoked", "expired", "failed"]
    remaining_seconds: float = Field(ge=0, le=60)
    token: SecretStr | None = Field(default=None, repr=False)
    password: SecretStr | None = Field(default=None, repr=False)


class Settlement(Model):
    generation: Generation
    input_cleanup_complete: Literal[True]


type Reply = Description | AccessGrant | Settlement


class Envelope(Model):
    result: Reply | None = None
    error: str | None = None


def command(request: Request) -> list[str]:
    encoded = base64.b64encode(request.model_dump_json().encode()).decode()
    invocation = shlex.join(["python3", "-I", SCRIPT, "call", "--root", ROOT, "--request", encoded])
    # Preserve the helper's bounded JSON error even when it exits nonzero. Missing
    # or malformed output is still an unknown outcome, never successful revocation.
    return [
        "sh",
        "-c",
        f"if test -r {SCRIPT}; then {invocation} || true; else printf '%s' '{{\"error\":\"endpoint_unavailable\"}}'; fi",
    ]


def decode(provider: str, request: Request, raw: str | bytes) -> Reply:
    """Bound and validate untrusted guest output without including it in diagnostics."""
    try:
        if len(raw) > 16384:
            raise ValueError
        envelope = Envelope.model_validate_json(raw)
        if envelope.error is not None:
            category = Category.CONFLICT
            known = {
                "stale_generation",
                "stale_epoch",
                "controller_exists",
                "grant_not_active",
                "grant_not_found",
                "grant_conflict",
                "capacity_exceeded",
            }
            if isinstance(request, Describe) and envelope.error in {
                "endpoint_unavailable",
                "unsupported_operation",
                "unsupported_protocol_version",
            }:
                category = Category.UNSUPPORTED
                code = "desktop_access_unavailable"
            elif envelope.error in known:
                code = "desktop_access_" + envelope.error
            else:
                code, category = "desktop_access_unconfirmed", Category.UNKNOWN_OUTCOME
            raise provider_error(
                provider,
                code,
                category,
                certainty=Certainty.UNKNOWN if category == Category.UNKNOWN_OUTCOME else Certainty.KNOWN,
            )
        reply = envelope.result
        if isinstance(request, Describe):
            if not isinstance(reply, Description):
                raise ValueError
        elif isinstance(request, Settle):
            if not isinstance(reply, Settlement) or reply.generation != request.generation:
                raise ValueError
        elif (
            not isinstance(reply, AccessGrant)
            or reply.generation != request.generation
            or reply.id != request.id
            or reply.epoch != request.epoch
        ):
            raise ValueError
        elif isinstance(request, Grant):
            if reply.mode != request.mode or (
                reply.state == "active" and (reply.token is None or reply.password is None)
            ):
                raise ValueError
        if isinstance(reply, AccessGrant):
            if reply.state != "active" and (reply.token is not None or reply.password is not None):
                raise ValueError
            if not isinstance(request, Grant) and (reply.token is not None or reply.password is not None):
                raise ValueError
            if (
                isinstance(request, Inspect)
                and request.operation == "revoke"
                and reply.state not in {"revoked", "expired", "failed"}
            ):
                raise ValueError
            if isinstance(request, Renew) and reply.state != "active":
                raise ValueError
        assert reply is not None
        return reply
    except ValueError:
        raise provider_error(
            provider, "desktop_access_unconfirmed", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
        ) from None
