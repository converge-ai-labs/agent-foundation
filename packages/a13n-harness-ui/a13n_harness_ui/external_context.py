"""Host-owned business data, independent of execution authority and checkpoints."""

from a13n_harness.context import RequestContext
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class ExternalContext(BaseModel):
    """A complete replacement value; an empty object clears the Thread context.

    Integrations supply JSON business/correlation metadata and optional request
    headers. Credentials and authorization continue to use their existing owners.
    Values are private to trusted runtime consumers, not automatically prompted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    headers: dict[str, str] = Field(default_factory=dict, repr=False)

    @field_validator("headers")
    @classmethod
    def normalize_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return dict(RequestContext(headers=value).headers)

    def request_context(self) -> RequestContext:
        return RequestContext(headers=self.headers)
