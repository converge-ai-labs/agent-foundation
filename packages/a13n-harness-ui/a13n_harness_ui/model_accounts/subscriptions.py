"""Public subscription account inputs and credential-free account/group views."""

from pydantic import BaseModel, ConfigDict, Field

from a13n_harness_ui.configuration.models import SubscriptionGroupResource

from .managed import Adoption, SubscriptionAccount, SubscriptionProvider
from .models import AccountProjection
from .usage_cache import UsageSnapshot


class SubscriptionAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,62}$")
    name: str = Field(min_length=1, max_length=256)


class SubscriptionAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool | None = None


class SubscriptionAccountView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account: SubscriptionAccount
    authentication: AccountProjection
    usage: UsageSnapshot


class SubscriptionCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accounts: tuple[SubscriptionAccountView, ...]
    groups: tuple[SubscriptionGroupResource, ...]
    adoption: dict[SubscriptionProvider, Adoption]
