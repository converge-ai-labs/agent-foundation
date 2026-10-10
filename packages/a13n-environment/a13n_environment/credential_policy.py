"""Declarative credential presence shared by definitions, hosts, and forms."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class CredentialMode(StrEnum):
    required = "required"
    optional = "optional"
    forbidden = "forbidden"


class CredentialPolicyCase(BaseModel):
    """Override credential presence for one declared configuration field value."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    field: Annotated[str, Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")]
    equals: str | int | bool | None
    mode: CredentialMode


class CredentialPolicy(BaseModel):
    """Declare credential presence requirements; no remote authentication is performed."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: CredentialMode = CredentialMode.required
    cases: tuple[CredentialPolicyCase, ...] = ()

    @model_validator(mode="after")
    def unique_cases(self):
        keys = [(case.field, type(case.equals), case.equals) for case in self.cases]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate credential policy condition")
        return self

    def resolve(self, configuration: BaseModel | dict[str, JsonValue]) -> CredentialMode:
        values = (
            configuration.model_dump(mode="json", by_alias=True)
            if isinstance(configuration, BaseModel)
            else configuration
        )
        matches = {
            case.mode
            for case in self.cases
            if type(values.get(case.field)) is type(case.equals) and values.get(case.field) == case.equals
        }
        if len(matches) > 1:
            raise ValueError("conflicting credential policy conditions")
        return next(iter(matches), self.mode)

    def validate_presence(self, configuration: BaseModel | dict[str, JsonValue], configured: bool) -> None:
        mode = self.resolve(configuration)
        if mode is CredentialMode.required and not configured:
            raise ValueError("the provider credential is required")
        if mode is CredentialMode.forbidden and configured:
            raise ValueError("the provider does not accept a credential")

    def validate_configuration_model(self, model: type[BaseModel]) -> None:
        fields = {field.alias or name for name, field in model.model_fields.items()}
        if any(case.field not in fields for case in self.cases):
            raise ValueError("credential policy conditions must name configuration fields")
