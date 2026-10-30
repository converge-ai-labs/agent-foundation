"""Supplier lifecycle and access; page execution stays in Harness.

Recovery inspects the original allocation key. An absent result after an uncertain
create does not authorize another create. Viewer revocation must cover existing
connections, not merely prevent opening a new viewer.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Literal, Protocol

from a13n_harness.capabilities.browser.cdp import Chromium
from a13n_harness.providers.definition import ProviderDefinition
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import BaseModel, JsonValue


@dataclass(frozen=True, slots=True)
class LiveBrowser:
    # Portable supplier state, encrypted by Service; never part of a resource read.
    state: dict[str, JsonValue] = field(repr=False)
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ViewerGrant:
    url: str = field(repr=False)
    state: dict[str, JsonValue] = field(repr=False)
    expires_at: datetime


class BrowserProvider(Protocol):
    async def create(self, allocation_id: str, recipe: BaseModel) -> LiveBrowser: ...

    async def inspect(self, allocation_id: str, state: dict[str, JsonValue] | None) -> LiveBrowser | None:
        """Find the same live allocation, never allocate or restart it.

        None means no live allocation was found; transport or inspection failures raise.
        After a previously confirmed creation, None confirms resource loss. After an
        uncertain creation, it does not prove a delayed create can no longer finish.
        """
        ...

    async def delete(self, allocation_id: str, state: dict[str, JsonValue] | None) -> None:
        """Idempotently confirm termination, including an uncertain or delayed create.

        Successful deletion permanently fences the allocation identity: an earlier
        create still in flight cannot leave a live session after this returns.
        """
        ...

    async def connect(self, state: dict[str, JsonValue]) -> Chromium:
        """Borrow a browser-level CDP connection; close disconnects, not terminates."""
        ...

    async def ensure_viewer(self, state: dict[str, JsonValue], grant_id: str, *, expires_at: datetime) -> ViewerGrant:
        """Create or extend the same input grant toward an absolute UTC deadline.

        Replays and out-of-order calls never shorten or duplicate the grant. The
        returned expiry covers all input connections, not only the viewer URL.
        Never grant input past the greatest requested deadline, or reopen an
        expired/revoked grant. A newer ensure may already have extended the expiry
        returned to an older caller. Revocation permanently fences pending ensures.
        An ensure cannot create or revive a terminated allocation.
        """
        ...

    async def revoke_viewer(self, state: dict[str, JsonValue], grant_id: str) -> None:
        """Confirm all input connections are revoked, including uncertain grants.

        Revocation permanently fences this grant identity: a late ensure request
        must not reopen it. Providers without that guarantee cannot offer viewers.
        """
        ...

    async def close(self) -> None: ...


class BrowserProviderError(Exception):
    def __init__(self, code: str, *, certainty: Literal["not_dispatched", "known", "unknown"] = "unknown"):
        super().__init__("Browser provider operation failed")
        self.code = code
        self.certainty = certainty


@dataclass(frozen=True, slots=True, kw_only=True)
class BrowserProviderDefinition[C: BaseModel, K: BaseModel](ProviderDefinition[C, K]):
    DOMAIN: ClassVar[str] = "Browser"
    recipe_model: type[BaseModel]
    backend_identity: Callable[[C], JsonValue]
    provider_factory: Callable[[C, K | None, EndpointPolicy], Awaitable[BrowserProvider]]
    supports_viewer: bool = False

    def validate_domain(self) -> None:
        if not isinstance(self.recipe_model, type) or not issubclass(self.recipe_model, BaseModel):
            raise TypeError("Browser recipes require a Pydantic model")
        if not callable(self.backend_identity) or not callable(self.provider_factory):
            raise TypeError("Browser providers require identity and factory functions")

    async def open_provider(self, config: object, credential: object, policy: EndpointPolicy) -> BrowserProvider:
        parsed = self.configuration_model.model_validate(config)
        return await self.provider_factory(parsed, self.parse_credential(parsed, credential), policy)
