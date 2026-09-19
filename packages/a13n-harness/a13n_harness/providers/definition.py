"""The shared core every Provider definition declares, in every domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import BaseModel, JsonValue

from .authentication import Authentication, CredentialMode
from .validation import validate_definition

NO_CREDENTIAL = Authentication(mode=CredentialMode.forbidden)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderDefinition[C: BaseModel, K: BaseModel]:
    """Identity, declared inputs and setup help shared by all five Provider domains.

    `credential_model` is `None` for Providers that take no credential at all; such a
    definition declares no `Authentication`, because it can only forbid credentials.
    """

    DOMAIN: ClassVar[str]

    type: str
    display_name: str
    configuration_model: type[C]
    credential_model: type[K] | None = None
    authentication: Authentication = field(default_factory=Authentication)
    setup_url: str | None = None
    setup_label: str | None = None

    def __post_init__(self) -> None:
        validate_definition(
            self.DOMAIN,
            self.type,
            self.display_name,
            setup_url=self.setup_url,
            setup_label=self.setup_label,
            configuration_model=self.configuration_model,
            credential_model=self.credential_model,
        )
        if self.credential_model is None:
            if self.authentication not in (Authentication(), NO_CREDENTIAL):
                raise ValueError(
                    f"{self.DOMAIN} Provider {self.type!r} declares no credential model and cannot accept one"
                )
            object.__setattr__(self, "authentication", NO_CREDENTIAL)
        self.authentication.validate_configuration_model(self.configuration_model)
        self.validate_domain()

    def validate_domain(self) -> None:
        """Check the rules this domain adds to the shared core."""

    def parse_credential(self, configuration: C | dict[str, JsonValue], credential: object) -> K | None:
        """Enforce the declared presence rule, then parse a credential this Provider accepts."""
        self.authentication.validate_presence(configuration, credential is not None)
        if credential is None or self.credential_model is None:
            return None
        return self.credential_model.model_validate(credential)
