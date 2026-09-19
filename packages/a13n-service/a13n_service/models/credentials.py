"""Service-owned encrypted Model Provider secret bundle."""

from a13n_harness.providers.model.headers import ExtraHeaders
from pydantic import BaseModel, ConfigDict, Field


class ProviderSecrets(BaseModel):
    """One encrypted bundle; public presence metadata never contains its values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    credential: dict[str, object] | None = Field(default=None, repr=False)
    extra_headers: ExtraHeaders = Field(default_factory=dict, repr=False)

    def encrypted_value(self) -> str | None:
        return self.model_dump_json() if self.credential is not None or self.extra_headers else None
