"""Typed connection inputs without host resource identities or execution state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from a13n_harness.model_affinity import SessionAffinityHeader


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)] | None = Field(
        default=None, title="Base URL", description="Leave empty to use the Provider's default endpoint."
    )
    session_affinity_header: SessionAffinityHeader | None = Field(
        default=None,
        title="Session affinity header",
        description="Optional gateway header carrying a stable UUID derived by the host from its Thread ID.",
    )

    @property
    def authentication_headers(self) -> tuple[str, ...]:
        return ()


class ValidatedProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    configuration: dict[str, object]
    endpoint: str | None


@dataclass(frozen=True, slots=True)
class ModelConnection[C: ProviderConfiguration, K: BaseModel]:
    type: str
    configuration: C
    endpoint: str | None
    credential: K | None = field(repr=False)
    extra_headers: dict[str, str] = field(default_factory=dict, repr=False)
