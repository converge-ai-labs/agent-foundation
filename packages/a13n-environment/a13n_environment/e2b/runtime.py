"""Host-owned live credentials and stable create correlation."""

from dataclasses import dataclass, field

from pydantic import SecretStr

from .configuration import E2BBackendConfiguration, E2BCredential


@dataclass(frozen=True, slots=True)
class E2BProviderRuntime:
    api_key: SecretStr = field(repr=False)
    domain: str = "e2b.dev"
    managed: bool = True
    operation_id: str | None = None
    api_url: str | None = None

    def __post_init__(self) -> None:
        E2BCredential(api_key=self.api_key)
        E2BBackendConfiguration(domain=self.domain, api_url=self.api_url)
